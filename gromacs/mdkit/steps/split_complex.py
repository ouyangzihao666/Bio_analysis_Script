"""PyMOL complex splitting: complex PDB -> protein PDB + per-ligand PDBs."""

from __future__ import annotations

import os

from mdkit import gro
from mdkit.exceptions import StepError
from mdkit.steps.base import Step


class SplitComplexStep(Step):
    name = "split_complex"
    version = "1.0"
    description = "用 PyMOL 将复合物拆分为蛋白与内嵌配体（同名配体按 resid 区分）"
    inputs = []
    outputs = []
    param_schema = {}
    env_requirements = ["pymol"]

    def resolve_inputs(self, system, registry=None) -> list:
        if system.complex is None:
            return []
        return ["complex_pdb"]

    def _check_allowed(self, ctx) -> None:
        if ctx.system.complex is None and not ctx.system.has_ligands:
            raise StepError(
                "纯蛋白体系不应包含拆分步骤 split_complex：请使用纯蛋白工作流，"
                "或从工作流中移除该步骤"
            )

    def _verify_input(self, ctx) -> None:
        """Re-check the complex file at run time (guards against edits)."""
        path = ctx.get_input("complex_pdb")
        scan = gro.scan_pdb_residues(path)
        models = scan.pop("models", 0)
        if models > 1:
            raise StepError(
                "复合物 PDB 包含 %d 个 MODEL（多分子 PDB 不支持）: %s"
                % (models, path)
            )
        for ligand in ctx.system.ligands:
            recs = scan.get(ligand.gmx_name, [])
            found = any(
                r["resid"] == ligand.resid
                and ((not ligand.chain) or r["chain"] == ligand.chain)
                for r in recs
            )
            if not found:
                available = "、".join(
                    "%s%s%d" % (r["chain"], "" if not r["chain"] else ":", r["resid"])
                    for r in recs
                ) or "（无）"
                raise StepError(
                    "复合物中找不到配体 %s 的残基（%s%s），可用: %s（%s）"
                    % (
                        ligand.name,
                        ligand.gmx_name,
                        " chain %s" % ligand.chain if ligand.chain else "",
                        available,
                        path,
                    )
                )

    def _write_script(self, ctx) -> str:
        system = ctx.system
        script = ctx.path("split_complex.py")
        lines = [
            "from pymol import cmd",
            "cmd.load(%r, 'c')" % os.path.abspath(system.complex["file"]),
            "cmd.remove('c and solvent')",
        ]
        selections = []
        for i, ligand in enumerate(system.ligands):
            sel = "resn %s and resi %d" % (ligand.gmx_name, ligand.resid)
            if ligand.chain:
                sel += " and chain %s" % ligand.chain
            selections.append(sel)
            out = os.path.abspath(ctx.path("%s.pdb" % ligand.name))
            lines.append("cmd.create('lig_%d', 'c and %s')" % (i, sel))
            lines.append("cmd.save(%r, 'lig_%d')" % (out, i))
        lines.append(
            "cmd.remove('c and (%s)')" % " or ".join(selections)
        )
        protein_out = os.path.abspath(
            ctx.path("%s_split_protein.pdb" % system.name)
        )
        lines.append("cmd.save(%r, 'c')" % protein_out)
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return script

    def run(self, ctx) -> None:
        self._check_allowed(ctx)
        if ctx.system.complex is None:
            ctx.log.info("无 complex 输入，split_complex 跳过")
            return
        self._verify_input(ctx)
        script = self._write_script(ctx)
        self.exec_commands(ctx)
        for ligand in ctx.system.ligands:
            out = ctx.path("%s.pdb" % ligand.name)
            if not os.path.isfile(out):
                raise StepError("PyMOL 未生成配体输出 %s" % out)
        protein_out = ctx.path("%s_split_protein.pdb" % ctx.system.name)
        if not os.path.isfile(protein_out):
            raise StepError("PyMOL 未生成蛋白输出 %s" % protein_out)

    def build_commands(self, ctx):
        self._check_allowed(ctx)
        if ctx.system.complex is None:
            return []
        for ligand in ctx.system.ligands:
            ctx.register_output(
                "split_ligand_pdb:%s" % ligand.name, "%s.pdb" % ligand.name
            )
        ctx.register_output(
            "split_protein_pdb", "%s_split_protein.pdb" % ctx.system.name
        )
        return [("cmd", ["pymol", "-cq", ctx.path("split_complex.py")], None)]
