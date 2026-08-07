"""Protein preparation: multimer merge, water removal, pdb2gmx."""

from __future__ import annotations

import os

from mdkit import gro
from mdkit.steps.base import Step


class ProteinPrepStep(Step):
    name = "protein_prep"
    version = "1.0"
    description = "pdb2gmx 生成蛋白结构与拓扑（支持多链多聚体合并）"
    inputs = []
    outputs = [
        ("processed_gro", "{system}_processed.gro", False),
        ("topol_top", "{system}_topol.top", False),
        ("posre_itp", "posre.itp", True),
    ]
    param_schema = {
        "force_field": {"type": str, "default": "amber99sb-ildn"},
        "water_model": {"type": str, "default": "tip3p"},
        "ignh": {"type": bool, "default": True},
        "remove_water": {"type": bool, "default": True},
        "ph": {"type": float, "default": None},
        "chainsep": {"type": str, "default": ""},
    }
    env_requirements = ["gmx"]

    def resolve_inputs(self, system) -> list:
        logicals = ["protein_pdb"]
        logicals += [
            "protein_chain:%d" % i for i in range(len(system.protein.chains))
        ]
        return logicals

    def run(self, ctx) -> None:
        system = ctx.system
        protein = system.protein
        if len(protein.chains) > 1:
            merged = ctx.path("%s_merged.pdb" % system.name)
            n = gro.merge_pdb_chains(
                protein.chains, merged, remove_water=ctx.params["remove_water"]
            )
            ctx.log.info("多链合并完成，共 %d 个原子 -> %s", n, merged)
            source_pdb = merged
        else:
            source_pdb = protein.chains[0]
            if ctx.params["remove_water"]:
                clean = ctx.path("%s_clean.pdb" % system.name)
                _strip_water(source_pdb, clean)
                source_pdb = clean

        if ctx.params.get("ph") is not None:
            ctx.log.warning(
                "pH=%.1f 为预留参数：当前版本不执行质子化调整，请自行预处理结构",
                ctx.params["ph"],
            )

        processed = ctx.register_output(
            "processed_gro", "%s_processed.gro" % system.name
        )
        topol = ctx.register_output("topol_top", "%s_topol.top" % system.name)
        args = [
            "pdb2gmx",
            "-f",
            source_pdb,
            "-o",
            processed,
            "-p",
            topol,
            "-water",
            ctx.params["water_model"],
            "-ff",
            ctx.params["force_field"],
        ]
        if ctx.params["ignh"]:
            args.append("-ignh")
        if ctx.params.get("chainsep"):
            args += ["-chainsep", ctx.params["chainsep"]]
        ctx.run_gmx(args, stdin_text="0\n")
        if os.path.isfile(ctx.path("posre.itp")):
            ctx.register_output("posre_itp", "posre.itp")
        ctx.log.info("蛋白预处理完成: %s", processed)


def _strip_water(src: str, dst: str) -> None:
    with open(src, "r", encoding="utf-8", errors="replace") as fh_in, open(
        dst, "w", encoding="utf-8"
    ) as fh_out:
        for line in fh_in:
            if line.startswith(("ATOM", "HETATM")):
                resname = line[17:20].strip().upper()
                if resname in gro.WATER_RES:
                    continue
            fh_out.write(line)
