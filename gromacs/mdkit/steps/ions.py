"""Add ions (grompp + genion)."""

from __future__ import annotations

from mdkit import topology
from mdkit.steps.base import Step


class IonsStep(Step):
    name = "ions"
    version = "1.1"
    description = "grompp + genion 添加离子中和体系"
    inputs = ["solv_gro", "solvated_top"]
    outputs = [
        ("ions_gro", "{system}_solv_ions.gro", False),
        ("ions_top", "{system}_ions.top", False),
        ("ions_tpr", "{system}_ions.tpr", False),
    ]
    param_schema = {
        "positive_ion": {"type": str, "default": "NA"},
        "negative_ion": {"type": str, "default": "CL"},
        "concentration": {"type": float, "default": 0.15},
        "neutral": {"type": bool, "default": True},
        "mdp": {"type": str, "default": "ions"},
        "mdp_overrides": {"type": dict, "default": {}},
        "maxwarn": {"type": int, "default": 5},
    }
    env_requirements = ["gmx"]

    def run(self, ctx) -> None:
        solv = ctx.get_input("solv_gro")
        top = ctx.get_input("solvated_top")
        ions_gro = ctx.register_output(
            "ions_gro", "%s_solv_ions.gro" % ctx.system.name
        )
        ions_top = ctx.register_output("ions_top", "%s_ions.top" % ctx.system.name)
        ions_tpr = ctx.register_output("ions_tpr", "%s_ions.tpr" % ctx.system.name)
        topology.absolutize_includes(top, ions_top)
        mdp_path, _mdp_info = ctx.render_mdp(
            ctx.params["mdp"], ctx.params["mdp_overrides"], "ions.mdp"
        )
        ctx.run_gmx(
            [
                "grompp",
                "-maxwarn",
                str(ctx.params["maxwarn"]),
                "-f",
                mdp_path,
                "-c",
                solv,
                "-p",
                ions_top,
                "-o",
                ions_tpr,
            ]
        )
        genion = [
            "genion",
            "-s",
            ions_tpr,
            "-o",
            ions_gro,
            "-p",
            ions_top,
            "-pname",
            ctx.params["positive_ion"],
            "-nname",
            ctx.params["negative_ion"],
            "-conc",
            str(ctx.params["concentration"]),
        ]
        if ctx.params["neutral"]:
            genion.append("-neutral")
        ctx.run_gmx(genion, stdin_text="SOL\n")
