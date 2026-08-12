"""MD analysis steps: rmsd / rmsf / gyrate / hbond / dssp / outpdb."""

from __future__ import annotations

import os

from mdkit.exceptions import InputError
from mdkit.steps.base import Step


class _AnalysisStep(Step):
    env_requirements = ["gmx"]
    optional_inputs = ["corrected_xtc", "md_xtc", "index_ndx"]
    param_schema = {
        "index": {"type": str, "default": ""},
        "tu": {"type": str, "default": "ns"},
    }

    def _ndx_args(self, ctx):
        ndx = ctx.params.get("index")
        if not ndx:
            ndx = ctx.registry.get("index_ndx")
        if ndx and os.path.isfile(ndx):
            return ["-n", ndx]
        return []

    def _traj(self, ctx):
        return ctx.registry.require_traj(self.name)

    def run(self, ctx) -> None:
        self.exec_commands(ctx)

    def resolve_inputs(self, system, registry=None) -> list:
        logicals = ["md_tpr"]
        if registry is None:
            logicals += ["corrected_xtc", "md_xtc"]
        elif registry.get("corrected_xtc"):
            logicals.append("corrected_xtc")
        elif registry.get("md_xtc"):
            logicals.append("md_xtc")
        else:
            logicals.append("md_xtc")
        logicals.append("index_ndx")
        return logicals


class RmsdStep(_AnalysisStep):
    name = "rmsd"
    version = "1.0"
    description = "RMSD 分析"
    inputs = ["md_tpr"]
    param_schema = dict(_AnalysisStep.param_schema)
    param_schema["fit_group"] = {"type": str, "default": "C-alpha"}
    param_schema["cal_group"] = {"type": str, "default": "C-alpha"}
    outputs = [("rmsd_xvg", "{system}_rmsd.xvg", False)]

    def build_commands(self, ctx):
        tpr = ctx.get_input("md_tpr")
        out = ctx.register_output("rmsd_xvg", "%s_rmsd.xvg" % ctx.system.name)
        return [
            (
                "gmx",
                ["rms", "-s", tpr, "-f", self._traj(ctx), "-o", out, "-tu", ctx.params["tu"]]
                + self._ndx_args(ctx),
                "%s\n%s\n" % (ctx.params["fit_group"], ctx.params["cal_group"]),
            )
        ]


class RmsfStep(_AnalysisStep):
    name = "rmsf"
    version = "1.0"
    description = "RMSF 分析"
    inputs = ["md_tpr"]
    param_schema = dict(_AnalysisStep.param_schema)
    param_schema["cal_group"] = {"type": str, "default": "Protein"}
    outputs = [("rmsf_xvg", "{system}_rmsf.xvg", False)]

    def build_commands(self, ctx):
        tpr = ctx.get_input("md_tpr")
        out = ctx.register_output("rmsf_xvg", "%s_rmsf.xvg" % ctx.system.name)
        return [
            (
                "gmx",
                ["rmsf", "-s", tpr, "-f", self._traj(ctx), "-o", out, "-res"]
                + self._ndx_args(ctx),
                "%s\n" % ctx.params["cal_group"],
            )
        ]


class GyrateStep(_AnalysisStep):
    name = "gyrate"
    version = "1.0"
    description = "回转半径分析"
    inputs = ["md_tpr"]
    param_schema = dict(_AnalysisStep.param_schema)
    param_schema["cal_group"] = {"type": str, "default": "Protein"}
    outputs = [("gyrate_xvg", "{system}_gyrate.xvg", False)]

    def build_commands(self, ctx):
        tpr = ctx.get_input("md_tpr")
        out = ctx.register_output("gyrate_xvg", "%s_gyrate.xvg" % ctx.system.name)
        return [
            (
                "gmx",
                ["gyrate", "-s", tpr, "-f", self._traj(ctx), "-o", out, "-tu", ctx.params["tu"]]
                + self._ndx_args(ctx),
                "%s\n" % ctx.params["cal_group"],
            )
        ]


class HbondStep(_AnalysisStep):
    name = "hbond"
    version = "1.0"
    description = "氢键分析（默认蛋白-配体）"
    inputs = ["md_tpr"]
    param_schema = dict(_AnalysisStep.param_schema)
    param_schema["ref_group"] = {"type": str, "default": "Protein"}
    param_schema["target_group"] = {"type": str, "default": "Ligand"}
    outputs = [("hbond_xvg", "{system}_hbnum.xvg", False)]

    def build_commands(self, ctx):
        tpr = ctx.get_input("md_tpr")
        out = ctx.register_output("hbond_xvg", "%s_hbnum.xvg" % ctx.system.name)
        return [
            (
                "gmx",
                ["hbond", "-s", tpr, "-f", self._traj(ctx), "-num", out, "-tu", ctx.params["tu"]]
                + self._ndx_args(ctx),
                "%s\n%s\n" % (ctx.params["ref_group"], ctx.params["target_group"]),
            )
        ]


class DsspStep(_AnalysisStep):
    name = "dssp"
    version = "1.0"
    description = "二级结构分析"
    inputs = ["md_tpr"]
    param_schema = dict(_AnalysisStep.param_schema)
    param_schema["cal_group"] = {"type": str, "default": "Protein"}
    outputs = [
        ("dssp_dat", "{system}_dssp.dat", False),
        ("dssp_num", "{system}_dssp_num.xvg", False),
    ]

    def build_commands(self, ctx):
        tpr = ctx.get_input("md_tpr")
        dat = ctx.register_output("dssp_dat", "%s_dssp.dat" % ctx.system.name)
        num = ctx.register_output("dssp_num", "%s_dssp_num.xvg" % ctx.system.name)
        return [
            (
                "gmx",
                ["dssp", "-s", tpr, "-f", self._traj(ctx), "-o", dat, "-num", num, "-tu", ctx.params["tu"]]
                + self._ndx_args(ctx),
                "%s\n" % ctx.params["cal_group"],
            )
        ]


class OutpdbStep(_AnalysisStep):
    name = "outpdb"
    version = "1.0"
    description = "导出 PDB 结构"
    inputs = ["md_tpr"]
    param_schema = dict(_AnalysisStep.param_schema)
    param_schema["out_group"] = {"type": str, "default": "System"}
    param_schema["structure"] = {"type": str, "default": "corrected_gro"}
    outputs = [("outpdb_pdb", "{system}_md_corrected.pdb", False)]

    def build_commands(self, ctx):
        tpr = ctx.get_input("md_tpr")
        structure_key = ctx.params["structure"]
        structure = ctx.registry.get(structure_key)
        if not structure or not os.path.isfile(structure):
            raise InputError(
                "outpdb 找不到结构文件: %s" % structure_key,
                details={"logical": structure_key},
            )
        out = ctx.register_output("outpdb_pdb", "%s_md_corrected.pdb" % ctx.system.name)
        return [
            (
                "gmx",
                ["trjconv", "-s", tpr, "-f", structure, "-o", out] + self._ndx_args(ctx),
                "%s\n" % ctx.params["out_group"],
            )
        ]
