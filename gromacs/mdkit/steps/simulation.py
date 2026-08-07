"""Energy minimization, NVT/NPT equilibration and production MD."""

from __future__ import annotations

import shlex

from mdkit.steps.base import Step


class _SimulationStep(Step):
    """Shared grompp + mdrun behaviour."""

    param_schema = {
        "mdp": {"type": str, "default": "md"},
        "mdp_overrides": {"type": dict, "default": {}},
        "maxwarn": {"type": int, "default": 5},
        "verbose": {"type": bool, "default": False},
        "nt": {"type": int, "default": None},
        "gpu_id": {"type": str, "default": ""},
        "rdd": {"type": str, "default": ""},
        "extra_args": {"type": str, "default": ""},
        "continue_cpt": {"type": str, "default": ""},
        "timeout": {"type": float, "default": None},
    }
    env_requirements = ["gmx"]

    def _run_mdrun(self, ctx, deffnm: str, tpr: str) -> None:
        args = ["mdrun", "-deffnm", deffnm, "-s", tpr]
        if ctx.params["verbose"]:
            args.append("-v")
        if ctx.params.get("nt"):
            args += ["-nt", str(ctx.params["nt"])]
        if ctx.params.get("gpu_id"):
            args += ["-gpu_id", ctx.params["gpu_id"]]
        if ctx.params.get("rdd"):
            args += ["-rdd", ctx.params["rdd"]]
        extra = ctx.params.get("extra_args") or ""
        if extra:
            args += shlex.split(extra)
        if ctx.params.get("continue_cpt"):
            args += ["-cpi", ctx.params["continue_cpt"]]
        ctx.run_gmx(args, timeout=ctx.params.get("timeout"))


class EmStep(_SimulationStep):
    name = "em"
    version = "1.0"
    description = "能量最小化"
    inputs = ["ions_gro", "ions_top"]
    param_schema = dict(_SimulationStep.param_schema)
    param_schema["mdp"] = {"type": str, "default": "minim"}
    outputs = [
        ("em_tpr", "{system}_em.tpr", False),
        ("em_gro", "{system}_em.gro", False),
        ("em_edr", "{system}_em.edr", False),
        ("em_log", "{system}_em.log", False),
        ("em_cpt", "{system}_em.cpt", True),
    ]

    def run(self, ctx) -> None:
        gro_in = ctx.get_input("ions_gro")
        top = ctx.get_input("ions_top")
        tpr = ctx.register_output("em_tpr", "%s_em.tpr" % ctx.system.name)
        mdp_path, _ = ctx.render_mdp(
            ctx.params["mdp"], ctx.params["mdp_overrides"], "minim.mdp"
        )
        ctx.run_gmx(
            [
                "grompp",
                "-maxwarn",
                str(ctx.params["maxwarn"]),
                "-f",
                mdp_path,
                "-c",
                gro_in,
                "-p",
                top,
                "-o",
                tpr,
            ]
        )
        ctx.register_output("em_gro", "%s_em.gro" % ctx.system.name)
        ctx.register_output("em_edr", "%s_em.edr" % ctx.system.name)
        ctx.register_output("em_log", "%s_em.log" % ctx.system.name)
        ctx.register_output("em_cpt", "%s_em.cpt" % ctx.system.name, optional=True)
        self._run_mdrun(ctx, "%s_em" % ctx.system.name, tpr)


class NvtStep(_SimulationStep):
    name = "nvt"
    version = "1.0"
    description = "NVT 平衡"
    inputs = ["em_gro", "ions_top"]
    param_schema = dict(_SimulationStep.param_schema)
    param_schema["mdp"] = {"type": str, "default": "nvt"}
    outputs = [
        ("nvt_tpr", "{system}_nvt.tpr", False),
        ("nvt_gro", "{system}_nvt.gro", False),
        ("nvt_edr", "{system}_nvt.edr", False),
        ("nvt_log", "{system}_nvt.log", False),
        ("nvt_cpt", "{system}_nvt.cpt", False),
        ("nvt_xtc", "{system}_nvt.xtc", True),
        ("nvt_trr", "{system}_nvt.trr", True),
    ]

    def run(self, ctx) -> None:
        gro_in = ctx.get_input("em_gro")
        top = ctx.get_input("ions_top")
        tpr = ctx.register_output("nvt_tpr", "%s_nvt.tpr" % ctx.system.name)
        mdp_path, _ = ctx.render_mdp(
            ctx.params["mdp"], ctx.params["mdp_overrides"], "nvt.mdp"
        )
        ctx.run_gmx(
            [
                "grompp",
                "-maxwarn",
                str(ctx.params["maxwarn"]),
                "-f",
                mdp_path,
                "-c",
                gro_in,
                "-r",
                gro_in,
                "-p",
                top,
                "-o",
                tpr,
            ]
        )
        for logical, suffix, optional in (
            ("nvt_gro", "gro", False),
            ("nvt_edr", "edr", False),
            ("nvt_log", "log", False),
            ("nvt_cpt", "cpt", False),
            ("nvt_xtc", "xtc", True),
            ("nvt_trr", "trr", True),
        ):
            ctx.register_output(
                logical, "%s_nvt.%s" % (ctx.system.name, suffix), optional=optional
            )
        self._run_mdrun(ctx, "%s_nvt" % ctx.system.name, tpr)


class NptStep(_SimulationStep):
    name = "npt"
    version = "1.0"
    description = "NPT 平衡"
    inputs = ["nvt_gro", "nvt_cpt", "ions_top"]
    param_schema = dict(_SimulationStep.param_schema)
    param_schema["mdp"] = {"type": str, "default": "npt"}
    outputs = [
        ("npt_tpr", "{system}_npt.tpr", False),
        ("npt_gro", "{system}_npt.gro", False),
        ("npt_edr", "{system}_npt.edr", False),
        ("npt_log", "{system}_npt.log", False),
        ("npt_cpt", "{system}_npt.cpt", False),
        ("npt_xtc", "{system}_npt.xtc", True),
        ("npt_trr", "{system}_npt.trr", True),
    ]

    def run(self, ctx) -> None:
        gro_in = ctx.get_input("nvt_gro")
        cpt_in = ctx.get_input("nvt_cpt")
        top = ctx.get_input("ions_top")
        tpr = ctx.register_output("npt_tpr", "%s_npt.tpr" % ctx.system.name)
        mdp_path, _ = ctx.render_mdp(
            ctx.params["mdp"], ctx.params["mdp_overrides"], "npt.mdp"
        )
        ctx.run_gmx(
            [
                "grompp",
                "-maxwarn",
                str(ctx.params["maxwarn"]),
                "-f",
                mdp_path,
                "-c",
                gro_in,
                "-r",
                gro_in,
                "-t",
                cpt_in,
                "-p",
                top,
                "-o",
                tpr,
            ]
        )
        for logical, suffix, optional in (
            ("npt_gro", "gro", False),
            ("npt_edr", "edr", False),
            ("npt_log", "log", False),
            ("npt_cpt", "cpt", False),
            ("npt_xtc", "xtc", True),
            ("npt_trr", "trr", True),
        ):
            ctx.register_output(
                logical, "%s_npt.%s" % (ctx.system.name, suffix), optional=optional
            )
        self._run_mdrun(ctx, "%s_npt" % ctx.system.name, tpr)


class MdStep(_SimulationStep):
    name = "md"
    version = "1.0"
    description = "生产 MD 模拟"
    inputs = ["npt_gro", "npt_cpt", "ions_top"]
    param_schema = dict(_SimulationStep.param_schema)
    outputs = [
        ("md_tpr", "{system}_md.tpr", False),
        ("md_gro", "{system}_md.gro", False),
        ("md_edr", "{system}_md.edr", False),
        ("md_log", "{system}_md.log", False),
        ("md_cpt", "{system}_md.cpt", False),
        ("md_xtc", "{system}_md.xtc", True),
    ]

    def run(self, ctx) -> None:
        gro_in = ctx.get_input("npt_gro")
        cpt_in = ctx.get_input("npt_cpt")
        top = ctx.get_input("ions_top")
        tpr = ctx.register_output("md_tpr", "%s_md.tpr" % ctx.system.name)
        mdp_path, _ = ctx.render_mdp(
            ctx.params["mdp"], ctx.params["mdp_overrides"], "md.mdp"
        )
        ctx.run_gmx(
            [
                "grompp",
                "-maxwarn",
                str(ctx.params["maxwarn"]),
                "-f",
                mdp_path,
                "-c",
                gro_in,
                "-t",
                cpt_in,
                "-p",
                top,
                "-o",
                tpr,
            ]
        )
        for logical, suffix, optional in (
            ("md_gro", "gro", False),
            ("md_edr", "edr", False),
            ("md_log", "log", False),
            ("md_cpt", "cpt", False),
            ("md_xtc", "xtc", True),
        ):
            ctx.register_output(
                logical, "%s_md.%s" % (ctx.system.name, suffix), optional=optional
            )
        self._run_mdrun(ctx, "%s_md" % ctx.system.name, tpr)
