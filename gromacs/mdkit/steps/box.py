"""Define the simulation box (editconf)."""

from __future__ import annotations

from mdkit.steps.base import Step


class BoxStep(Step):
    name = "box"
    version = "1.0"
    description = "editconf 定义模拟盒子"
    inputs = []
    outputs = [("box_gro", "{system}_waterbox.gro", False)]
    param_schema = {
        "box_type": {
            "type": str,
            "default": "cubic",
            "choices": ["cubic", "dodecahedron", "octahedron", "triclinic"],
        },
        "box_distance": {"type": float, "default": 1.2},
        "center": {"type": bool, "default": True},
    }
    env_requirements = ["gmx"]

    def run(self, ctx) -> None:
        src = ctx.registry.require_structure(self.name)
        out = ctx.register_output("box_gro", "%s_waterbox.gro" % ctx.system.name)
        args = [
            "editconf",
            "-f",
            src,
            "-o",
            out,
            "-d",
            str(ctx.params["box_distance"]),
            "-bt",
            ctx.params["box_type"],
        ]
        if ctx.params["center"]:
            args.append("-c")
        ctx.run_gmx(args)

    def resolve_inputs(self, system) -> list:
        return ["complex_gro"] if system.has_ligands else ["processed_gro"]
