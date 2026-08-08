"""Queue manifest for the ctl / watch controller.

The queue is a single JSON file (``queue.json``) in the work-dir-base.
All mutations happen under ``.mdkit.queue.lock`` with atomic writes, so a
running ``mdkit watch`` and concurrent ``mdkit ctl`` calls never corrupt
each other.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from typing import Callable, Optional

from mdkit.exceptions import ConfigError, RunError


QUEUE_FILE = "queue.json"
QUEUE_LOCK = ".mdkit.queue.lock"


class QueueLock:
    """Exclusive non-blocking lock for queue mutations."""

    def __init__(self, queue_path: str):
        self.path = os.path.join(os.path.dirname(queue_path), QUEUE_LOCK)
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
                    try:
                        self._fh.close()
                    except Exception:
                        pass
                    self._fh = None
                    raise RunError(
                        "队列已被其他进程锁定: %s" % self.path
                    )
                time.sleep(0.1)

    def release(self) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None


def atomic_write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".queue_", dir=os.path.dirname(path))
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


def new_item(name: str, slot: Optional[int], run_dir: str) -> dict:
    return {
        "name": name,
        "slot": slot,
        "status": "queued",
        "attempts": 0,
        "run_dir": run_dir,
        "force": False,
        "note": None,
        "blocked_since": None,
        "pending_intervention": None,
        "template": None,
        "started_at": None,
        "finished_at": None,
        "last_exit": None,
    }


TERMINAL = ("done", "repair-timeout")


class Queue:
    """Load / save / lock a queue manifest."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def load(self) -> dict:
        if not self.exists():
            raise ConfigError("队列不存在: %s（先用 mdkit ctl init 创建）" % self.path)
        with open(self.path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if "queue" not in data or "items" not in data:
            raise ConfigError("队列文件格式错误: %s" % self.path)
        return data

    def save(self, data: dict) -> None:
        atomic_write_json(self.path, data)

    def locked(self, fn: Callable[[dict], dict], timeout: float = 0.0) -> dict:
        """Run fn(data) under the queue lock and persist the result."""
        lock = QueueLock(self.path)
        lock.acquire(timeout=timeout)
        try:
            data = self.load()
            out = fn(data)
            self.save(data)
            return out
        finally:
            lock.release()


def run_locked(run_dir: str) -> bool:
    """Non-destructive probe: is an mdkit run process holding the run lock?"""
    from mdkit.monitor import RunLock

    lock = RunLock(run_dir)
    try:
        lock.acquire(timeout=0.0)
        return False
    except RunError:
        return True
    finally:
        lock.release()


def default_queue_path(work_dir_base: str) -> str:
    return os.path.join(os.path.abspath(work_dir_base), QUEUE_FILE)
