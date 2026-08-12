"""Generate a custom index with protein / ligand / water / ion groups."""

from __future__ import annotations

from mdkit import gro
from mdkit.exceptions import StepError
from mdkit.steps.base import Step


class IndexStep(Step):
    name = "index"
    version = "1.0"
    description = "生成自定义索引（每配体独立组，供校正与分析使用）"
    inputs = ["ions_gro", "processed_gro"]
    outputs = [("index_ndx", "{system}.ndx", False)]
    param_schema = {}

    def _ion_names(self, ctx):
        ions = ctx.step_params("ions")
        if ions is None:
            raise StepError(
                "index 步骤需要工作流中的 ions 步骤提供阴阳离子名称"
                "（positive_ion/negative_ion），但未找到 ions 步骤"
            )
        pos = ions.get("positive_ion")
        neg = ions.get("negative_ion")
        if not pos or not neg:
            raise StepError(
                "生成 Ion 索引组需要显式配置 positive_ion/negative_ion，"
                "未提供默认值"
            )
        return (pos, neg)

    def run(self, ctx) -> None:
        structure = ctx.get_input("ions_gro")
        protein_gro = ctx.get_input("processed_gro")
        protein_atoms = gro.read_gro(protein_gro)["natoms"]
        ligand_counts = []
        if ctx.system.has_ligands:
            complex_gro = ctx.get_input("complex_gro")
            merged = gro.read_gro(complex_gro)
            for ligand in ctx.system.ligands:
                ligand_gro = ctx.registry.require(
                    "ligand_gro:%s" % ligand.name, self.name
                )
                ligand_counts.append(gro.read_gro(ligand_gro)["natoms"])
        out = ctx.register_output("index_ndx", "%s.ndx" % ctx.system.name)
        n = gro.build_index(
            structure,
            protein_atoms,
            ligand_counts,
            [l.name for l in ctx.system.ligands],
            out,
            self._ion_names(ctx),
        )
        ctx.log.info("索引生成完成（%d 原子）: %s", n, out)

    def resolve_inputs(self, system, registry=None) -> list:
        logicals = list(self.inputs)
        if system.has_ligands:
            logicals.append("complex_gro")
        for ligand in system.ligands:
            logicals.append("ligand_gro:%s" % ligand.name)
        return logicals
