"""Merge protein + ligands into one structure and topology.

Topology side: each unique GROMACS molecule (``gmx_name``) is included via
its own ``<name>_GMX.itp`` file (one include per group; ``[ atomtypes ]``
is kept only in the first include and stripped from the rest, with atom
types introduced by later groups merged into the first itp). Structure
side: every extracted ligand copy is concatenated in order — no dedup, so
the total atom count always matches the ``[ molecules ]`` counts.
"""

from __future__ import annotations

import os

from mdkit import gro, topology
from mdkit.exceptions import ConfigError
from mdkit.steps.base import Step


class ComplexMergeStep(Step):
    name = "complex_merge"
    version = "2.0"
    description = "蛋白-配体结构与拓扑合并（同名配体按 count 合并）"
    inputs = ["processed_gro", "topol_top"]
    outputs = [
        ("complex_gro", "{system}_complex.gro", False),
        ("complex_top", "{system}_complex.top", False),
    ]
    param_schema = {}

    def run(self, ctx) -> None:
        system = ctx.system
        if not system.has_ligands:
            ctx.log.info("无配体，complex_merge 跳过（结构/拓扑沿用蛋白预处理结果）")
            return
        protein_gro = self.get_input_paths(ctx)["processed_gro"]
        protein_top = self.get_input_paths(ctx)["topol_top"]
        ligand_gros = []
        for ligand in system.ligands:
            ligand_gros.append(ctx.registry.require("ligand_gro:%s" % ligand.name, self.name))

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
        groups = _group_by_gmx_name(system.ligands)
        include_paths = []
        kept_types = {}
        first_itp = None
        first_types = None
        for group in groups:
            gmx_name = group[0].gmx_name
            rep = group[0]
            src_itp = ctx.registry.require("ligand_itp:%s" % rep.name, self.name)
            rel = "%s_GMX.itp" % rep.name
            stage_itp = ctx.path(rel)
            topology.prepare_ligand_itp(src_itp, stage_itp, kept_types)
            if first_itp is None:
                first_itp = stage_itp
                first_types = set(kept_types)
            ctx.register_output("ligand_itp_copy:%s" % rep.name, rel)
            include_paths.append(os.path.join(ctx.step_dir, rel))
        # GROMACS 2026 requires a single [ atomtypes ] section: merge atom
        # types introduced by later ligand itps into the first itp so they
        # are not silently dropped (e.g. FDME's os/cc/cd/c/o/ha types).
        if first_itp is not None and first_types is not None:
            extra = {
                name: line
                for name, line in kept_types.items()
                if name not in first_types
            }
            if extra:
                topology.append_atomtypes(first_itp, extra)
        # The include must point to the post-commit location (the stage dir
        # is deleted after the step commits).
        topology.insert_includes_after_first(complex_top, include_paths)
        topology.append_molecules(
            complex_top,
            [(group[0].gmx_name, len(group)) for group in groups],
        )
        ctx.log.info("拓扑合并完成: %s", complex_top)

    def get_input_paths(self, ctx):
        return {
            "processed_gro": ctx.get_input("processed_gro"),
            "topol_top": ctx.get_input("topol_top"),
        }

    def _resolve_inputs(self, system, registry) -> list:
        logicals = list(self.inputs)
        for ligand in system.ligands:
            logicals.append("ligand_gro:%s" % ligand.name)
            logicals.append("ligand_itp:%s" % ligand.name)
        return logicals

    def resolve_inputs(self, system, registry=None) -> list:
        return self._resolve_inputs(system, registry)


def _group_by_gmx_name(ligands):
    """Group ligands by gmx_name, preserving first-occurrence order."""
    groups = []
    index = {}
    for ligand in ligands:
        if ligand.gmx_name not in index:
            index[ligand.gmx_name] = len(groups)
            groups.append([ligand])
        else:
            groups[index[ligand.gmx_name]].append(ligand)
    return groups
