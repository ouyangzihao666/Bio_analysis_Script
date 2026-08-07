"""Trajectory PBC correction (whole -> nojump -> center/pbc)."""

from __future__ import annotations

import os

from mdkit.exceptions import StepError
from mdkit.steps.base import Step


class TrajCorrectStep(Step):
    name = "traj_correct"
    version = "1.0"
    description = "轨迹周期性校正：whole → nojump → center + pbc + compact"
    inputs = ["md_tpr", "md_gro", "index_ndx"]
    outputs = [
        ("corrected_xtc", "{system}_md_corrected.xtc", False),
        ("corrected_gro", "{system}_md_corrected.gro", False),
    ]
    param_schema = {
        "method": {
            "type": str,
            "default": "pbc_mol",
            "choices": ["pbc_none", "pbc_nojump", "pbc_mol", "pbc_atom", "pbc_res"],
        },
        "whole_group": {"type": str, "default": "System"},
        "center_group": {"type": str, "default": "Protein"},
        "output_group": {"type": str, "default": "System"},
    }
    env_requirements = ["gmx"]

    def run(self, ctx) -> None:
        tpr = ctx.get_input("md_tpr")
        gro_in = ctx.get_input("md_gro")
        ndx = ctx.get_input("index_ndx")
        xtc = ctx.registry.get("md_xtc")
        if not xtc or not os.path.isfile(xtc):
            raise StepError("未找到生产轨迹 md_xtc，无法执行轨迹校正")
        system = ctx.system.name
        method = ctx.params["method"]

        corrected_xtc = ctx.register_output(
            "corrected_xtc", "%s_md_corrected.xtc" % system
        )
        corrected_gro = ctx.register_output(
            "corrected_gro", "%s_md_corrected.gro" % system
        )

        if method == "pbc_none":
            _copy_file(xtc, corrected_xtc)
            _copy_file(gro_in, corrected_gro)
            ctx.log.info("pbc_none：直接复制原始轨迹")
            return

        whole = ctx.path("%s_temp_whole.xtc" % system)
        first = ctx.path("%s_first_frame.gro" % system)
        nojump = ctx.path("%s_temp_nojump.xtc" % system)
        self.exec_commands(ctx)
        if method == "pbc_nojump":
            os.replace(nojump, corrected_xtc)
            _copy_file(gro_in, corrected_gro)
        for tmp in (whole, first, nojump):
            if os.path.isfile(tmp):
                ctx.remove_temp(os.path.basename(tmp))
        ctx.log.info("轨迹校正完成（%s）", method)

    def build_commands(self, ctx):
        tpr = ctx.get_input("md_tpr")
        gro_in = ctx.get_input("md_gro")
        ndx = ctx.get_input("index_ndx")
        xtc = ctx.registry.get("md_xtc")
        if not xtc or (not ctx.registry.preview_mode and not os.path.isfile(xtc)):
            return []
        system = ctx.system.name
        method = ctx.params["method"]
        corrected_xtc = ctx.register_output(
            "corrected_xtc", "%s_md_corrected.xtc" % system
        )
        corrected_gro = ctx.register_output(
            "corrected_gro", "%s_md_corrected.gro" % system
        )
        if method == "pbc_none":
            return []
        whole = ctx.path("%s_temp_whole.xtc" % system)
        first = ctx.path("%s_first_frame.gro" % system)
        nojump = ctx.path("%s_temp_nojump.xtc" % system)
        whole_g = ctx.params["whole_group"]
        cmds = [
            ("gmx", ["trjconv", "-f", xtc, "-s", tpr, "-o", whole, "-pbc", "whole", "-n", ndx], "%s\n" % whole_g),
            ("gmx", ["trjconv", "-f", whole, "-s", tpr, "-dump", "0", "-o", first, "-n", ndx], "%s\n" % whole_g),
            ("gmx", ["trjconv", "-f", whole, "-s", first, "-o", nojump, "-pbc", "nojump", "-n", ndx], "%s\n" % whole_g),
        ]

        if method in ("pbc_mol", "pbc_atom", "pbc_res"):
            pbc_arg = method.split("_")[1]
            center = ctx.params["center_group"]
            output = ctx.params["output_group"]
            selection = "%s\n%s\n" % (center, output)
            cmds.append(
                ("gmx", ["trjconv", "-f", nojump, "-s", tpr, "-o", corrected_xtc, "-center", "-pbc", pbc_arg, "-ur", "compact", "-n", ndx], selection)
            )
            cmds.append(
                ("gmx", ["trjconv", "-f", gro_in, "-s", tpr, "-o", corrected_gro, "-center", "-pbc", pbc_arg, "-ur", "compact", "-n", ndx], selection)
            )
        return cmds


def _copy_file(src: str, dst: str) -> None:
    with open(src, "rb") as fh_in, open(dst, "wb") as fh_out:
        fh_out.write(fh_in.read())
