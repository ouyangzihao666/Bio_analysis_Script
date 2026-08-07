"""Ligand parameterization: obabel -> antechamber -> acpype (or manual)."""

from __future__ import annotations

import glob
import os
import shutil

from mdkit import gro, mol2, topology
from mdkit.exceptions import StepError
from mdkit.steps.base import Step


class LigandPrepStep(Step):
    name = "ligand_prep"
    version = "1.1"
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
        topology.rename_molecule(itp, gr, ligand.name)

    def _auto(self, ctx, ligand) -> None:
        fmt = ligand.resolved_format()
        if ligand.residue is not None:
            extracted = ctx.path("%s_lig.pdb" % ligand.name)
            n = gro.extract_pdb_residue(
                ligand.file, ligand.residue, extracted, ligand.name
            )
            ctx.log.info(
                "已从 PDB 提取配体 %s（残基 %s，%d 原子）",
                ligand.name,
                ligand.residue,
                n,
            )
            src = extracted
            fmt = "pdb"
        else:
            src = ctx.registry.get("ligand_sdf:%s" % ligand.name) or ligand.file
            if fmt == "mol2" and ligand.source_mol_index is not None:
                split_mol2 = ctx.path("%s_src.mol2" % ligand.name)
                mol2.extract_molecule(src, split_mol2, ligand.source_mol_index)
                src = split_mol2
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
        ctx.run_cmd(["acpype", "-i", mol2_out])
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
        topology.rename_molecule(itp, gr, ligand.name)
        ctx.remove_temp("%s.acpype" % os.path.basename(acpype_dir.rstrip("/")))
        ctx.remove_temp("%s_H.sdf" % ligand.name)
        ctx.remove_temp("%s.mol2" % ligand.name)
        if ligand.source_mol_index is not None:
            ctx.remove_temp("%s_src.mol2" % ligand.name)
        if ligand.residue is not None:
            ctx.remove_temp("%s_lig.pdb" % ligand.name)
