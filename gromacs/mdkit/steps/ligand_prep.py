"""Ligand parameterization: obabel -> antechamber -> acpype (or manual).

Splitting is NOT part of this step anymore: embedded ligands come from
``split_complex`` and multi-molecule files from ``split_ligand`` /
``pymol_split_ligand``. This step only receives single-molecule inputs.
"""

from __future__ import annotations

import glob
import os
import shutil

from mdkit import mol2, topology
from mdkit.exceptions import StepError
from mdkit.steps.base import Step


class LigandPrepStep(Step):
    name = "ligand_prep"
    version = "2.0"
    description = "配体加氢、电荷计算与 GROMACS 拓扑生成（GAFF2/手动）"
    inputs = []
    outputs = []
    param_schema = {
        "ligand_force_field": {"type": str, "default": "gaff2"},
        "charge_method": {"type": str, "default": "bcc"},
    }
    env_requirements = ["obabel", "antechamber", "parmchk2", "acpype"]

    def resolve_inputs_with(self, system, registry) -> list:
        logicals = []
        for ligand in system.ligands:
            if ligand.method == "manual":
                logicals.append("ligand_itp_src:%s" % ligand.name)
                logicals.append("ligand_gro_src:%s" % ligand.name)
                continue
            if registry is not None and registry.get("ligand_mol:%s" % ligand.name):
                logicals.append("ligand_mol:%s" % ligand.name)
            elif registry is not None and registry.get(
                "split_ligand_pdb:%s" % ligand.name
            ):
                logicals.append("split_ligand_pdb:%s" % ligand.name)
            else:
                logicals.append("ligand_sdf:%s" % ligand.name)
        return logicals

    def resolve_inputs(self, system, registry=None) -> list:
        return self.resolve_inputs_with(system, registry)

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
        topology.rename_molecule(itp, gr, ligand.gmx_name)

    def _auto(self, ctx, ligand) -> None:
        src = (
            ctx.registry.get("ligand_mol:%s" % ligand.name)
            or ctx.registry.get("split_ligand_pdb:%s" % ligand.name)
            or ctx.registry.get("ligand_sdf:%s" % ligand.name)
            or ligand.file
        )
        fmt = _fmt_for_src(src, ligand)
        if not os.path.isfile(src):
            raise StepError("配体 %s 的输入文件不存在: %s" % (ligand.name, src))
        sdf_h = ctx.path("%s_H.sdf" % ligand.name)
        mol2_out = ctx.path("%s.mol2" % ligand.name)
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
                fmt,
                src,
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
                mol2_out,
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
        mol2.rename_molecule_name(mol2_out, ligand.gmx_name)
        ctx.run_cmd(["acpype", "-i", mol2_out])
        candidates = sorted(glob.glob(ctx.path("%s*.acpype" % ligand.gmx_name)))
        if not candidates:
            raise StepError(
                "acpype 未生成 %s.acpype 目录（%s）。"
                "请检查 acpype 输出路径中的文件，确定 <配体名>_GMX.itp 的真实"
                "“配体名”（实际目录: %s）"
                % (
                    ligand.gmx_name,
                    ctx.cwd,
                    _acpype_dir_summary(ctx.cwd),
                )
            )
        acpype_dir = candidates[0]
        src_itp = os.path.join(acpype_dir, "%s_GMX.itp" % ligand.gmx_name)
        src_gro = os.path.join(acpype_dir, "%s_GMX.gro" % ligand.gmx_name)
        if not os.path.isfile(src_itp) or not os.path.isfile(src_gro):
            raise StepError(
                "acpype 输出缺少 %s_GMX.itp/gro（目录: %s）。"
                "请检查 acpype 输出路径中的文件，确定 <配体名>_GMX.itp 的真实"
                "“配体名”（实际文件: %s）"
                % (
                    ligand.gmx_name,
                    acpype_dir,
                    _acpype_dir_summary(acpype_dir),
                )
            )
        shutil.copyfile(src_itp, itp)
        shutil.copyfile(src_gro, gr)
        topology.rename_molecule(itp, gr, ligand.gmx_name)
        ctx.remove_temp("%s.acpype" % ligand.gmx_name)
        ctx.remove_temp("%s_H.sdf" % ligand.name)
        ctx.remove_temp("%s.mol2" % ligand.name)

    def build_commands(self, ctx):
        """Preview of the ligand toolchain (no files are created)."""
        cmds = []
        for ligand in ctx.system.ligands:
            if ligand.method == "manual":
                continue
            src = (
                ctx.registry.get("ligand_mol:%s" % ligand.name)
                or ctx.registry.get("split_ligand_pdb:%s" % ligand.name)
                or ctx.registry.get("ligand_sdf:%s" % ligand.name)
                or ligand.file
            )
            fmt = _fmt_for_src(src, ligand)
            sdf_h = ctx.path("%s_H.sdf" % ligand.name)
            mol2_out = ctx.path("%s.mol2" % ligand.name)
            cmds.append(
                ("cmd", ["obabel", "-i", fmt, src, "-o", "sdf", "-O", sdf_h, "-h"], None)
            )
            cmds.append(
                (
                    "cmd",
                    [
                        "antechamber",
                        "-i",
                        sdf_h,
                        "-fi",
                        "sdf",
                        "-o",
                        mol2_out,
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
                    ],
                    None,
                )
            )
            cmds.append(("cmd", ["acpype", "-i", mol2_out], None))
        return cmds


def _fmt_for_src(src: str, ligand) -> str:
    ext = os.path.splitext(src)[1].lower()
    if ext == ".mol2":
        return "mol2"
    if ext in (".sdf", ".sd"):
        return "sdf"
    if ext == ".pdb":
        return "pdb"
    return ligand.resolved_format()


def _acpype_dir_summary(directory: str) -> str:
    parts = []
    itps = sorted(glob.glob(os.path.join(directory, "*_GMX.itp")))
    parts.extend(os.path.basename(p) for p in itps)
    for d in sorted(glob.glob(os.path.join(directory, "*.acpype"))):
        inner = sorted(glob.glob(os.path.join(d, "*_GMX.itp")))
        if inner:
            parts.extend(
                os.path.join(os.path.basename(d), os.path.basename(p))
                for p in inner
            )
        else:
            parts.append(os.path.basename(d) + "/")
    return "、".join(parts) or "（目录为空）"
