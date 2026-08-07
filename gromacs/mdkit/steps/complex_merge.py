"""Merge protein + ligands into one structure and topology."""

from __future__ import annotations

from mdkit import gro, topology
from mdkit.exceptions import ConfigError
from mdkit.steps.base import Step


class ComplexMergeStep(Step):
    name = "complex_merge"
    version = "1.2"
    description = "蛋白-配体结构与拓扑合并（支持多配体、多拷贝）"
    inputs = ["processed_gro", "topol_top"]
    outputs = [
        ("complex_gro", "{system}_complex.gro", False),
        ("complex_top", "{system}_complex.top", False),
        ("ligands_merged_itp", "ligands_merged.itp", True),
    ]
    param_schema = {}

    def run(self, ctx) -> None:
        system = ctx.system
        if not system.has_ligands:
            raise ValueError(
                "complex_merge 步骤要求体系包含配体；纯蛋白流程不应包含该步骤"
            )
        protein_gro = self.get_input_paths(ctx)["processed_gro"]
        protein_top = self.get_input_paths(ctx)["topol_top"]
        ligand_gros = []
        ligand_itps = []
        for ligand in system.ligands:
            ligand_gros.append(ctx.registry.require("ligand_gro:%s" % ligand.name, self.name))
            ligand_itps.append(ctx.registry.require("ligand_itp:%s" % ligand.name, self.name))

        complex_gro = ctx.register_output("complex_gro", "%s_complex.gro" % system.name)
        info = gro.merge_gro(protein_gro, ligand_gros, complex_gro, system.name)
        ctx.log.info(
            "结构合并完成: %d 原子（蛋白 %d + 配体 %d）",
            info["natoms"],
            info["protein_atoms"],
            sum(info["ligand_atom_counts"]),
        )

        complex_top = ctx.register_output("complex_top", "%s_complex.top" % system.name)
        topology.absolutize_includes(protein_top, complex_top)
        merged_itp = ctx.path("ligands_merged.itp")
        topology.merge_ligand_itps(
            ligand_itps, [l.name for l in system.ligands], merged_itp
        )
        ctx.register_output("ligands_merged_itp", "ligands_merged.itp")
        topology.insert_includes_after_first(complex_top, [merged_itp])
        topology.append_molecules(
            complex_top, [(l.name, l.count) for l in system.ligands]
        )
        ctx.log.info("拓扑合并完成: %s", complex_top)

    def get_input_paths(self, ctx):
        return {
            "processed_gro": ctx.get_input("processed_gro"),
            "topol_top": ctx.get_input("topol_top"),
        }

    def resolve_inputs(self, system) -> list:
        logicals = list(self.inputs)
        for ligand in system.ligands:
            logicals.append("ligand_gro:%s" % ligand.name)
            logicals.append("ligand_itp:%s" % ligand.name)
        return logicals
