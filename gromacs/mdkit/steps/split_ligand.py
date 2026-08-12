"""Deterministic ligand splitting: parse mol2/sdf and write single molecules."""

from __future__ import annotations

from mdkit import ligsplit
from mdkit.steps._ligsplit import _LigandSplitStep


class SplitLigandStep(_LigandSplitStep):
    name = "split_ligand"
    version = "1.0"
    description = "按名称拆分多分子配体文件（mol2/sdf，确定性解析器）"
    env_requirements = []

    def _execute(self, ctx, targets) -> None:
        for ligand, idx, out in targets:
            fmt = ligand.resolved_format()
            ligsplit.extract_molecule(ligand.file, out, fmt, idx)
            ctx.log.info("配体 %s 已拆分（第 %d 个分子）-> %s", ligand.name, idx + 1, out)

    def _preview_commands(self, ctx, targets):
        # Deterministic extraction is pure Python; nothing external to preview.
        return []
