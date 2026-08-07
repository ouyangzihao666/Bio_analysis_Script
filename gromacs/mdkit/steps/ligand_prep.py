"""Ligand parameterization: obabel -> antechamber -> acpype (or manual)."""

from __future__ import annotations

import glob
import os
import shutil

from mdkit.exceptions import StepError
from mdkit.steps.base import Step


class LigandPrepStep(Step):
    name = "ligand_prep"
    version = "1.0"
    description = "配体加氢、电荷计算与 GROMACS 拓扑生成（GAFF2/手动）"
    inputs = []
    outputs = []
    param_schema = {
        "ligand_force_field": {"type": str, "default": "gaff2"},
        "charge_method": {"type": str, "default": "bcc"},
    }
    env_requirements = ["obabel", "antechamber", "parmchk2", "acpype"]

    def resolve_inputs(self, system) -> list:
        logicals = ["ligand_sdf:%s" % l.name for l in system.ligands]
        for ligand in system.ligands:
            if ligand.method == "manual":
                logicals.append("ligand_itp_src:%s" % ligand.name)
                logicals.append("ligand_gro_src:%s" % ligand.name)
        return logicals

    def run(self, ctx) -> None:
        system = ctx.system
        if not system.has_ligands:
            ctx.log.info("无配体，跳过配体预处理")
            return
        for ligand in system.ligands:
            if not os.path.isfile(ligand.file):
                raise StepError(
                    "配体 %s 文件不存在: %s" % (ligand.name, ligand.file)
                )
            if ligand.method == "manual":
                self._manual(ctx, ligand)
            else:
                self._auto(ctx, ligand)
            ctx.log.info("配体 %s 预处理完成", ligand.name)

    def _manual(self, ctx, ligand) -> None:
        itp = ctx.register_output(
            "ligand_itp:%s" % ligand.name, "%s_GMX.itp" % ligand.name
        )
        gr = ctx.register_output(
            "ligand_gro:%s" % ligand.name, "%s_GMX.gro" % ligand.name
        )
        shutil.copyfile(ligand.itp_file, itp)
        shutil.copyfile(ligand.gro_file, gr)

    def _auto(self, ctx, ligand) -> None:
        sdf_h = ctx.path("%s_H.sdf" % ligand.name)
        mol2 = ctx.path("%s.mol2" % ligand.name)
        itp = ctx.register_output(
            "ligand_itp:%s" % ligand.name, "%s_GMX.itp" % ligand.name
        )
        gr = ctx.register_output(
            "ligand_gro:%s" % ligand.name, "%s_GMX.gro" % ligand.name
        )
        ctx.run_cmd(
            [
                "obabel",
                "-i",
                "sdf",
                ligand.file,
                "-o",
                "sdf",
                "-O",
                sdf_h,
                "-h",
            ]
        )
        ctx.run_cmd(
            [
                "antechamber",
                "-i",
                sdf_h,
                "-fi",
                "sdf",
                "-o",
                mol2,
                "-fo",
                "mol2",
                "-at",
                ctx.params["ligand_force_field"],
                "-c",
                ctx.params["charge_method"],
                "-s",
                "2",
                "-nc",
                str(int(ligand.charge)),
            ]
        )
        ctx.run_cmd(["acpype", "-i", mol2])
        candidates = sorted(glob.glob(ctx.path("%s*.acpype" % ligand.name)))
        if not candidates:
            raise StepError(
                "acpype 未生成 %s.acpype 目录（%s）" % (ligand.name, ctx.cwd)
            )
        acpype_dir = candidates[0]
        src_itp = os.path.join(acpype_dir, "%s_GMX.itp" % ligand.name)
        src_gro = os.path.join(acpype_dir, "%s_GMX.gro" % ligand.name)
        if not os.path.isfile(src_itp) or not os.path.isfile(src_gro):
            raise StepError(
                "acpype 输出缺少 %s_GMX.itp/gro（目录: %s）" % (ligand.name, acpype_dir)
            )
        shutil.copyfile(src_itp, itp)
        shutil.copyfile(src_gro, gr)
        ctx.remove_temp("%s.acpype" % os.path.basename(acpype_dir.rstrip("/")))
        ctx.remove_temp("%s_H.sdf" % ligand.name)
        ctx.remove_temp("%s.mol2" % ligand.name)
