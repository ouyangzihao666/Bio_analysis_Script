"""Add ions (grompp + genion)."""

from __future__ import annotations

from mdkit import topology
from mdkit.steps.base import Step


class IonsStep(Step):
    name = "ions"
    version = "1.2"
    description = "grompp + genion 添加离子中和体系"
    inputs = ["solv_gro", "solvated_top"]
    outputs = [
        ("ions_gro", "{system}_solv_ions.gro", False),
        ("ions_top", "{system}_ions.top", False),
        ("ions_tpr", "{system}_ions.tpr", False),
    ]
    param_schema = {
        # No defaults on purpose: the index step builds the Ion group from
        # these names, so silently assuming NA/CL would mislabel ions.
        "positive_ion": {"type": str},
        "negative_ion": {"type": str},
        "concentration": {"type": float, "default": 0.15},
        "neutral": {"type": bool, "default": True},
        "mdp": {"type": str, "default": "ions"},
        "mdp_overrides": {"type": dict, "default": {}},
        "maxwarn": {"type": int, "default": 5},
    }
    env_requirements = ["gmx"]

    def mdp_signature(self, params: dict, mdp_dir: str):
        from mdkit.mdp import resolve_template, sha256_text

        spec = params.get("mdp")
        if not spec:
            return None
        template = resolve_template(spec, mdp_dir)
        with open(template, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        overrides = params.get("mdp_overrides") or {}
        return {
            "template": template,
            "template_sha256": sha256_text(text),
            "overrides": {str(k): str(v) for k, v in overrides.items()},
        }

    def run(self, ctx) -> None:
        top = ctx.get_input("solvated_top")
        ions_top = ctx.register_output("ions_top", "%s_ions.top" % ctx.system.name)
        topology.absolutize_includes(top, ions_top)
        ctx.render_mdp(ctx.params["mdp"], ctx.params["mdp_overrides"], "ions.mdp")
        self.exec_commands(ctx)

    def build_commands(self, ctx):
        solv = ctx.get_input("solv_gro")
        ions_gro = ctx.register_output(
            "ions_gro", "%s_solv_ions.gro" % ctx.system.name
        )
        ions_top = ctx.register_output("ions_top", "%s_ions.top" % ctx.system.name)
        ions_tpr = ctx.register_output("ions_tpr", "%s_ions.tpr" % ctx.system.name)
        mdp_path = ctx.path("ions.mdp")
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
        return [
            (
                "gmx",
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
                ],
                None,
            ),
            ("gmx", genion, "SOL\n"),
        ]
