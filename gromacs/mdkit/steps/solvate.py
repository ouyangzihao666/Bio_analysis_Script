"""Solvation step."""

from __future__ import annotations

from mdkit import topology
from mdkit.steps.base import Step


class SolvateStep(Step):
    name = "solvate"
    version = "1.1"
    description = "gmx solvate 添加溶剂（拓扑副本自包含，模板不被修改）"
    inputs = ["box_gro"]
    outputs = [
        ("solv_gro", "{system}_solv.gro", False),
        ("solvated_top", "{system}_solv.top", False),
    ]
    param_schema = {"solvent": {"type": str, "default": "spc216.gro"}}
    env_requirements = ["gmx"]

    def run(self, ctx) -> None:
        top = ctx.registry.require_top(self.name)
        solv_top = ctx.register_output(
            "solvated_top", "%s_solv.top" % ctx.system.name
        )
        topology.absolutize_includes(top, solv_top)
        self.exec_commands(ctx)

    def build_commands(self, ctx):
        box = ctx.get_input("box_gro")
        solv_gro = ctx.register_output("solv_gro", "%s_solv.gro" % ctx.system.name)
        solv_top = ctx.register_output(
            "solvated_top", "%s_solv.top" % ctx.system.name
        )
        return [
            (
                "gmx",
                [
                "solvate",
                "-cp",
                box,
                "-cs",
                ctx.params["solvent"],
                "-o",
                solv_gro,
                "-p",
                solv_top,
                ],
                None,
            )
        ]

    def resolve_inputs(self, system) -> list:
        return ["box_gro", "complex_top" if system.has_ligands else "topol_top"]
