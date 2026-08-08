"""Live mdrun progress parsing from step logs."""

from __future__ import annotations

import os
import re


def load_run_workflow(data: dict):
    """Workflow object recorded in a run_status.json, or None."""
    wf_path = (data.get("run") or {}).get("workflow")
    if not wf_path or not os.path.isfile(wf_path):
        return None
    try:
        from mdkit.config import load_workflow

        return load_workflow(wf_path)
    except Exception:
        return None


def step_progress(workflow, run_dir: str, system_name: str, step_name: str):
    """Current mdrun step/time from the live log, plus nsteps for a percentage."""
    try:
        from mdkit.runner import step_dir_for

        spec = workflow.step_by_name(step_name)
        if spec is None:
            return None
        step_dir = step_dir_for(workflow, run_dir, system_name, spec)
        log = os.path.join(step_dir, ".stage", "%s_%s.log" % (system_name, step_name))
        if not os.path.isfile(log):
            return None
        last = None
        in_table = False
        with open(log, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "Step" in line and "Time" in line:
                    in_table = True
                    continue
                if in_table:
                    m = re.match(r"^\s*(\d+)\s+([0-9.eE+-]+)\s*$", line)
                    if m:
                        last = (int(m.group(1)), float(m.group(2)))
                    else:
                        in_table = False
        if not last:
            return None
        nsteps = None
        for name in (
            "%s.mdp" % step_name,
            "minim.mdp",
            "nvt.mdp",
            "npt.mdp",
            "md.mdp",
            "ions.mdp",
        ):
            p = os.path.join(step_dir, ".stage", name)
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        m = re.match(r"^\s*nsteps\s*=\s*(\d+)", line)
                        if m:
                            nsteps = int(m.group(1))
                if nsteps:
                    break
        percent = (100.0 * last[0] / nsteps) if nsteps else None
        return {
            "step": last[0],
            "time_ps": last[1],
            "nsteps": nsteps,
            "percent": percent,
        }
    except Exception:
        return None
