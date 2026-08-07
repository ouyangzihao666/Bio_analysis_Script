"""Environment checks: tools, versions, mdp templates, dependencies."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import List

from mdkit import __version__


def _which(tool: str):
    return shutil.which(tool)


def gmx_version() -> str:
    gmx = _which("gmx")
    if not gmx:
        return ""
    try:
        out = subprocess.run(
            ["gmx", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        ).stdout.decode("utf-8", errors="replace")
        m = re.search(r"GROMACS version:\s+([0-9.]+)", out)
        return m.group(1) if m else "unknown"
    except Exception:
        return "unknown"


def check_environment(tools: List[str] = None) -> dict:
    """Return a report dict with one entry per check."""
    checks = []

    def add(name, ok, detail, required=False):
        checks.append(
            {"name": name, "ok": bool(ok), "detail": detail, "required": bool(required)}
        )

    try:
        import yaml  # noqa: F401

        add("PyYAML", True, "已安装", required=True)
    except ImportError:
        add("PyYAML", False, "未安装（pip install pyyaml）", required=True)

    gmx = _which("gmx")
    ver = gmx_version() if gmx else ""
    add("gmx", gmx, "%s（版本 %s）" % (gmx or "未找到", ver or "?"), required=True)
    if gmx and ver and ver != "unknown":
        major = int(ver.split(".")[0])
        if major < 2021:
            add("gmx 版本兼容", False, "mdkit 面向 GROMACS 2021+，当前 %s" % ver)

    for tool in tools or []:
        p = _which(tool)
        add("工具 %s" % tool, p, p or "未找到（PATH 中无 %s）" % tool)

    python = os.path.splitext(os.path.basename(__import__("sys").executable))[0]
    add("Python", True, "%s %s" % (python, __import__("platform").python_version()))

    return {
        "mdkit_version": __version__,
        "checks": checks,
        "exit_code": 1 if any(c["required"] and not c["ok"] for c in checks) else 0,
    }
