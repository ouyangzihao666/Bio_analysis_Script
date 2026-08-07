"""Logical file registry: steps communicate via logical names, not paths."""

from __future__ import annotations

import glob
import os
from typing import Dict, Optional

from mdkit.exceptions import InputError


DEFAULT_CONVENTIONS = {
    "md_tpr": "{run_dir}/{system}/*_md_production/{system}_md.tpr",
    "md_xtc": "{run_dir}/{system}/*_md_production/{system}_md.xtc",
    "md_gro": "{run_dir}/{system}/*_md_production/{system}_md.gro",
    "corrected_xtc": "{run_dir}/{system}/*_traj_correct/{system}_md_corrected.xtc",
    "corrected_gro": "{run_dir}/{system}/*_traj_correct/{system}_md_corrected.gro",
    "index_ndx": "{run_dir}/{system}/*_index/{system}.ndx",
    "ions_gro": "{run_dir}/{system}/*_ions/{system}_solv_ions.gro",
}


def conventions_with_dirs(dirs: dict) -> dict:
    conv = dict(DEFAULT_CONVENTIONS)
    for key, pattern in (dirs or {}).items():
        for logical, tpl in list(conv.items()):
            if "*_%s" % key in tpl:
                conv[logical] = tpl.replace("*_%s" % key, pattern)
    return conv


class FileRegistry:
    """Maps logical file names to absolute paths for one system."""

    def __init__(
        self,
        run_dir: str,
        system,
        conventions: Optional[Dict[str, str]] = None,
    ):
        self.run_dir = os.path.abspath(run_dir)
        self.system = system
        self._entries: Dict[str, str] = {}
        self._producers: Dict[str, str] = {}
        self.conventions = dict(DEFAULT_CONVENTIONS)
        if conventions:
            self.conventions.update(conventions)

    def register_source(self, logical: str, path: str, producer: str = "source") -> None:
        self._entries[logical] = os.path.abspath(path)
        self._producers[logical] = producer

    def set(self, logical: str, path: str, producer: Optional[str] = None) -> None:
        self._entries[logical] = os.path.abspath(path)
        if producer:
            self._producers[logical] = producer

    def producer(self, logical: str) -> Optional[str]:
        return self._producers.get(logical)

    def has(self, logical: str) -> bool:
        return logical in self._entries or self._resolve_convention(logical) is not None

    def get(self, logical: str) -> Optional[str]:
        if logical in self._entries:
            return self._entries[logical]
        return self._resolve_convention(logical)

    def require(self, logical: str, for_step: Optional[str] = None) -> str:
        path = self.get(logical)
        if path is None or not os.path.isfile(path):
            ctx = "步骤 %s " % for_step if for_step else ""
            raise InputError(
                "%s缺少输入文件: %s（已解析: %s）" % (ctx, logical, path or "未找到"),
                details={"logical": logical, "resolved": path},
            )
        return path

    def require_traj(self, for_step: Optional[str] = None) -> str:
        """Corrected trajectory preferred; falls back to the raw md xtc."""
        for logical in ("corrected_xtc", "md_xtc"):
            path = self.get(logical)
            if path and os.path.isfile(path):
                return path
        ctx = "步骤 %s " % for_step if for_step else ""
        raise InputError(
            "%s缺少轨迹文件（corrected_xtc / md_xtc 均未找到）" % ctx,
            details={"logical": "traj"},
        )

    def require_structure(self, for_step: Optional[str] = None) -> str:
        """Complex structure preferred; falls back to the processed protein."""
        for logical in ("complex_gro", "processed_gro"):
            path = self.get(logical)
            if path and os.path.isfile(path):
                return path
        ctx = "步骤 %s " % for_step if for_step else ""
        raise InputError(
            "%s缺少结构文件（complex_gro / processed_gro 均未找到）" % ctx,
            details={"logical": "structure"},
        )

    def require_top(self, for_step: Optional[str] = None) -> str:
        """Complex topology preferred; falls back to the processed protein top."""
        for logical in ("complex_top", "topol_top"):
            path = self.get(logical)
            if path and os.path.isfile(path):
                return path
        ctx = "步骤 %s " % for_step if for_step else ""
        raise InputError(
            "%s缺少拓扑文件（complex_top / topol_top 均未找到）" % ctx,
            details={"logical": "top"},
        )

    def _resolve_convention(self, logical: str) -> Optional[str]:
        tpl = self.conventions.get(logical)
        if not tpl:
            return None
        try:
            pattern = tpl.format(run_dir=self.run_dir, system=self.system.name)
        except (KeyError, ValueError):
            return None
        matches = glob.glob(pattern)
        if len(matches) == 1 and os.path.isfile(matches[0]):
            return matches[0]
        # Flat-layout fallback: same file directly in the system dir.
        if "*" in pattern:
            direct = os.path.normpath(
                "/".join(p for p in pattern.split("/") if "*" not in p)
            )
            if direct != pattern and os.path.isfile(direct):
                return direct
        return None
