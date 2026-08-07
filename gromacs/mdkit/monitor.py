"""Atomic JSON run-status storage with a per-run lock."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from typing import Callable, Optional

from mdkit.exceptions import RunError


STATUS_FILE = "run_status.json"
LOCK_FILE = ".mdkit.lock"


class RunLock:
    """Exclusive lock for a run directory (single runner process)."""

    def __init__(self, run_dir: str):
        self.path = os.path.join(run_dir, LOCK_FILE)
        self._fh = None

    def acquire(self, timeout: float = 0.0) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._fh = open(self.path, "w")
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fh.write(str(os.getpid()))
                self._fh.flush()
                return
            except OSError:
                if time.time() >= deadline:
                    raise RunError(
                        "run 目录已被其他进程锁定: %s（lock: %s）"
                        % (os.path.dirname(self.path), self.path)
                    )
                time.sleep(0.2)

    def release(self) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None


def atomic_write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".status_", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class RunState:
    """Load/save the run_status.json file."""

    def __init__(self, run_dir: str):
        self.run_dir = run_dir
        self.path = os.path.join(run_dir, STATUS_FILE)

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def load(self) -> dict:
        if not self.exists():
            return {}
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, data: dict) -> None:
        atomic_write_json(self.path, data)

    def update(self, fn: Callable[[dict], None]) -> dict:
        data = self.load()
        fn(data)
        self.save(data)
        return data


def new_step_state() -> dict:
    return {
        "status": "pending",
        "started_at": None,
        "finished_at": None,
        "duration_s": None,
        "exit_code": None,
        "error": None,
        "stderr_tail": None,
        "signature": None,
        "outputs": {},
        "note": None,
        "commands": [],
    }


def init_status(
    run_dir: str,
    run_name: str,
    workflow_path: str,
    systems_path: str,
    systems: list,
    step_names: list,
) -> dict:
    data = {
        "run": {
            "name": run_name,
            "workflow": os.path.abspath(workflow_path),
            "systems": os.path.abspath(systems_path),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "work_dir": os.path.abspath(run_dir),
        },
        "systems": {},
    }
    for system in systems:
        data["systems"][system.name] = {
            "status": "pending",
            "steps": {name: new_step_state() for name in step_names},
        }
    return data


def load_or_init_status(
    run_dir: str,
    run_name: str,
    workflow_path: str,
    systems_path: str,
    systems: list,
    step_names: list,
) -> dict:
    state = RunState(run_dir)
    if not state.exists():
        data = init_status(
            run_dir, run_name, workflow_path, systems_path, systems, step_names
        )
        state.save(data)
    else:
        data = state.load()
        # Merge any missing systems/steps (e.g. config grew).
        for system in systems:
            sys_entry = data.setdefault(
                "systems", {}
            ).setdefault(
                system.name,
                {"status": "pending", "steps": {}},
            )
            for name in step_names:
                sys_entry["steps"].setdefault(name, new_step_state())
        state.save(data)
    return data
