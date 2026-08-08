"""Concurrent run scheduler with resource slots.

A slot is an arbitrary set of extra mdrun arguments (e.g. GPU / pinoffset /
thread settings). mdkit never interprets these: it merges the user's
extra args and the slot args at the option level (deduplicated, last wins)
and injects the result into each system's md step before spawning
``mdkit run``.
"""

from __future__ import annotations

import copy
import os
import shlex
import subprocess
import sys
import time

import yaml

from mdkit.cliargs import merge_cli_options
from mdkit.config import load_systems, load_workflow, load_yaml
from mdkit.steps import load_steps
from mdkit.runner import effective_params


MDKIT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mdkit")


def parse_slots(slot_args, resources_path=None):
    """Build slot list from --slot args and/or a resources yaml."""
    slots = []
    if resources_path:
        data = load_yaml(resources_path)
        raw_slots = data.get("slots", []) or []
        for s in raw_slots:
            slots.append({"name": s.get("name", "slot_%d" % len(slots)), "args": s.get("args", "")})
    for arg in slot_args or []:
        slots.append({"name": "slot_%d" % len(slots), "args": arg})
    if not slots:
        slots = [{"name": "slot_0", "args": ""}]
    return slots


def slot_gpu(slot) -> str:
    tokens = shlex.split(slot.get("args", ""))
    for i, tok in enumerate(tokens):
        if tok == "-gpu_id" and i + 1 < len(tokens):
            return tokens[i + 1]
    return None


class SlotScheduler:
    """Runs a set of systems concurrently, one per free slot."""

    def __init__(self, workflow_path, systems_path, work_dir_base, log=None):
        self.workflow = load_workflow(workflow_path)
        self.systems_cfg = load_systems(systems_path)
        self.work_dir_base = os.path.abspath(work_dir_base)
        self.log = log
        self.steps = load_steps(self.workflow.resolve_steps_dir())
        self.md_spec = self.workflow.step_by_name("md")
        if self.md_spec is None:
            raise ValueError("workflow 中缺少 md 步骤，无法并发运行")

    def _merged_extra(self, system, slot) -> str:
        eff = effective_params(self.workflow, self.steps, system, self.md_spec)
        base = eff.get("extra_args") or ""
        merged = merge_cli_options(shlex.split(base), shlex.split(slot["args"]))
        return shlex.join(merged)

    def _write_temp_systems(self, system, merged_extra, path) -> None:
        # Rebuild the system entry from resolved absolute paths so the temp
        # file is independent of its own location.
        ligands = []
        for lig in system.ligands:
            entry = {
                "name": lig.name,
                "file": lig.file,
                "charge": lig.charge,
                "count": lig.count,
                "method": lig.method,
            }
            if lig.residue:
                entry["residue"] = lig.residue
            if lig.format != "auto":
                entry["format"] = lig.format
            if lig.names:
                entry["names"] = lig.names
            if lig.itp_file:
                entry["itp_file"] = lig.itp_file
            if lig.gro_file:
                entry["gro_file"] = lig.gro_file
            if not lig.split:
                entry["split"] = False
            ligands.append(entry)
        protein = (
            {"file": system.protein.chains[0]}
            if not system.protein.is_multimer
            else {"chains": system.protein.chains}
        )
        overrides = copy.deepcopy(system.overrides)
        overrides.setdefault("md", {})["extra_args"] = merged_extra
        data = {
            "systems": [
                {"name": system.name, "protein": protein, "ligands": ligands, "overrides": overrides}
            ]
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)

    def _spawn(self, system_name, slot, test_dir):
        system = self.systems_cfg.system_by_name(system_name)
        if system is None:
            raise ValueError("体系不存在: %s" % system_name)
        batch_dir = os.path.join(test_dir, ".batch")
        tmp_systems = os.path.join(batch_dir, "%s.yaml" % system_name)
        merged = self._merged_extra(system, slot)
        self._write_temp_systems(system, merged, tmp_systems)
        run_dir = os.path.join(test_dir, system_name)
        console = open(
            os.path.join(test_dir, "console_%s.log" % system_name), "w"
        )
        argv = [
            sys.executable,
            MDKIT_SCRIPT,
            "run",
            "-w",
            self.workflow.path,
            "-s",
            tmp_systems,
            "--system",
            system_name,
            "--work-dir",
            run_dir,
            "--json",
        ]
        proc = subprocess.Popen(argv, stdout=console, stderr=subprocess.STDOUT)
        return proc, run_dir, console

    def run_test(
        self,
        test_name: str,
        systems_names,
        slots,
        tick=None,
    ):
        """Run systems concurrently on the given slots.

        ``tick(running)`` is called each poll cycle with
        ``{name: {"rundir": ..., "slot": index}}`` for live systems.
        Returns (results, wall_s).
        """
        test_dir = os.path.join(self.work_dir_base, test_name)
        os.makedirs(test_dir, exist_ok=True)
        queue = list(systems_names)
        slots = list(slots)
        running = {}
        slot_free = list(range(len(slots)))
        results = {}
        start = time.time()
        while queue or running:
            while queue and slot_free:
                name = queue.pop(0)
                si = slot_free.pop(0)
                proc, run_dir, console = self._spawn(name, slots[si], test_dir)
                running[name] = {
                    "proc": proc,
                    "slot": si,
                    "rundir": run_dir,
                    "console": console,
                    "start": time.time(),
                }
                if self.log:
                    self.log.info(
                        "[%s] 启动体系 %s（槽位 %d: %s）",
                        test_name,
                        name,
                        si,
                        slots[si]["args"] or "(无参数)",
                    )
            finished = []
            for name, info in running.items():
                rc = info["proc"].poll()
                if rc is not None:
                    finished.append(name)
                    results[name] = {
                        "exit": rc,
                        "slot": info["slot"],
                        "slot_args": slots[info["slot"]]["args"],
                        "wall_s": round(time.time() - info["start"], 1),
                    }
            for name in finished:
                slot_free.append(running[name]["slot"])
                try:
                    running[name]["console"].close()
                except Exception:
                    pass
                del running[name]
            if tick:
                tick(running)
            if queue or running:
                time.sleep(5)
        wall = round(time.time() - start, 1)
        return results, wall, test_dir
