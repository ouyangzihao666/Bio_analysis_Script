"""PyMOL-based ligand splitting: extract molecules as discrete states."""

from __future__ import annotations

import os

from mdkit.exceptions import StepError
from mdkit.steps._ligsplit import _LigandSplitStep


class PymolSplitLigandStep(_LigandSplitStep):
    name = "pymol_split_ligand"
    version = "1.0"
    description = "按名称拆分多分子配体文件（mol2/sdf，PyMOL state 提取）"
    env_requirements = ["pymol"]

    def _group_by_file(self, targets):
        groups = {}
        for ligand, idx, out in targets:
            groups.setdefault(ligand.file, []).append((ligand, idx, out))
        return groups

    def _write_script(self, ctx, file_path, group, script_path):
        lines = [
            "from pymol import cmd",
            "cmd.load(%r, 'm', discrete=1)" % os.path.abspath(file_path),
            "cmd.split_states('m', prefix='m_')",
        ]
        for _ligand, idx, out in sorted(group, key=lambda t: t[1]):
            lines.append(
                "cmd.save(%r, 'm_%04d')" % (os.path.abspath(out), idx + 1)
            )
        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def _execute(self, ctx, targets) -> None:
        for file_path, group in self._group_by_file(targets).items():
            script = ctx.path("pymol_split_%s.py" % os.path.basename(file_path))
            self._write_script(ctx, file_path, group, script)
            ctx.run_cmd(["pymol", "-cq", script])
            for ligand, idx, out in group:
                if not os.path.isfile(out):
                    raise StepError(
                        "PyMOL 未生成拆分输出 %s（第 %d 个分子）" % (out, idx + 1)
                    )
                ctx.log.info(
                    "配体 %s 已拆分（第 %d 个分子）-> %s",
                    ligand.name,
                    idx + 1,
                    out,
                )

    def _preview_commands(self, ctx, targets):
        cmds = []
        for file_path, group in self._group_by_file(targets).items():
            script = ctx.path("pymol_split_%s.py" % os.path.basename(file_path))
            cmds.append(("cmd", ["pymol", "-cq", script], None))
        return cmds
