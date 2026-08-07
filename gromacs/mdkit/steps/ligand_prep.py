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
    version = "1.2"
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
        self._check_components(ctx, ligand, itp)

    def _auto(self, ctx, ligand) -> None:
        fmt = ligand.resolved_format()
        if ligand.residue is not None:
            extracted = ctx.path("%s_lig.pdb" % ligand.name)
            n = gro.extract_pdb_residue(
                ligand.file, ligand.residue, extracted, ligand.name
            )
            components = gro.count_pdb_residue_components(ligand.file, ligand.residue)
            if components > 1:
                raise StepError(
                    "PDB 残基 %s（配体 %s）包含 %d 个互不连接的分子片段，"
                    "说明多个小分子共用了同一残基名，无法自动拆分。"
                    "解决方案：人工拆分后为每个小分子提供独立的 sdf/mol2 文件，"
                    "或改用不同残基名。" % (ligand.residue, ligand.name, components)
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
            elif fmt == "mol2":
                blocks = mol2.parse_molecules(src)
                if len(blocks) == 1 and mol2.count_components_in_block(blocks[0]) > 1:
                    raise StepError(
                        "配体 %s 的 mol2 单分子段包含 %d 个互不连接的分子片段，"
                        "说明多个小分子被合并到了同一名称下，无法自动拆分。"
                        "解决方案：人工拆分后为每个小分子提供独立的 sdf/mol2 文件。"
                        % (ligand.name, mol2.count_components_in_block(blocks[0]))
                    )
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
        self._check_components(ctx, ligand, itp)
        ctx.remove_temp("%s.acpype" % os.path.basename(acpype_dir.rstrip("/")))
        ctx.remove_temp("%s_H.sdf" % ligand.name)
        ctx.remove_temp("%s.mol2" % ligand.name)
        if ligand.source_mol_index is not None:
            ctx.remove_temp("%s_src.mol2" % ligand.name)
        if ligand.residue is not None:
            ctx.remove_temp("%s_lig.pdb" % ligand.name)

    def _check_components(self, ctx, ligand, itp_path: str) -> None:
        natoms, n_components = topology.count_components(itp_path)
        if n_components > 1:
            raise StepError(
                "配体 %s 的拓扑包含 %d 个互不连接的分子片段（共 %d 原子），"
                "说明输入把多个小分子合并到了同一残基名下，无法自动拆分。"
                "解决方案：人工拆分后为每个小分子提供独立的 sdf/mol2 文件"
                "（或 PDB 内使用不同残基名），再重新运行。"
                % (ligand.name, n_components, natoms)
            )

    def build_commands(self, ctx):
        """Preview of the ligand toolchain (no files are created)."""
        cmds = []
        for ligand in ctx.system.ligands:
            fmt = ligand.resolved_format()
            if ligand.residue is not None:
                src = ctx.path("%s_lig.pdb" % ligand.name)
                fmt = "pdb"
            else:
                src = ctx.registry.get("ligand_sdf:%s" % ligand.name) or ligand.file
                if fmt == "mol2" and ligand.source_mol_index is not None:
                    src = ctx.path("%s_src.mol2" % ligand.name)
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
