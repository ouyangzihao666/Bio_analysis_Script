"""Shared logic for the ligand-splitting steps (split_ligand backends)."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from mdkit import gro, ligsplit
from mdkit.config import Ligand
from mdkit.exceptions import ChoiceError, ConfigError, StepError
from mdkit.steps.base import Step


class _LigandSplitStep(Step):
    """Base for deterministic / PyMOL ligand splitting."""

    name = ""
    description = ""
    inputs = []
    outputs = []
    param_schema = {}

    def resolve_inputs(self, system, registry=None) -> list:
        if system.complex is not None:
            return []
        return ["ligand_sdf:%s" % l.name for l in system.ligands]

    def _check_allowed(self, ctx) -> None:
        if not ctx.system.has_ligands:
            raise StepError(
                "纯蛋白体系不应包含拆分步骤 %s：请使用纯蛋白工作流，"
                "或从工作流中移除该步骤" % self.name
            )

    def _molecules(self, ligand) -> List[Dict]:
        fmt = ligand.resolved_format()
        return ligsplit.parse_molecules(ligand.file, fmt)

    def _first_ambiguous(self, names, molecules):
        supply = {}
        for m in molecules:
            supply[m["name"]] = supply.get(m["name"], 0) + 1
        demand = {}
        for n in names:
            demand[n] = demand.get(n, 0) + 1
        for n in names:
            if supply.get(n, 0) > demand.get(n, 0):
                return n
        return None

    def _pin_for(self, ctx, ligand, molecules):
        if not ctx.choice_answer:
            return None
        if not ligand.names:
            return None
        name = self._first_ambiguous(ligand.names, molecules)
        if name is None:
            return None
        try:
            idx = int(str(ctx.choice_answer).strip()) - 1
        except ValueError:
            return None
        return (name, idx)

    def _plan_targets(self, ctx):
        """Resolve per-ligand (target, output path) plans for multi-molecule files.

        Registers ``ligand_mol:<name>`` outputs and returns a list of
        (ligand, molecule_index, out_path) tuples. Raises StepError /
        ChoiceError for files that need user attention.
        """
        targets = []
        for ligand in list(ctx.system.ligands):
            if ligand.method == "manual":
                continue
            fmt = ligand.resolved_format()
            if fmt == "pdb":
                models = gro.count_pdb_models(ligand.file)
                if models > 1:
                    raise StepError(
                        "配体 %s 的 PDB 文件包含 %d 个 MODEL（多分子 PDB 不支持，"
                        "请先拆分为单 MODEL 文件）: %s"
                        % (ligand.name, models, ligand.file)
                    )
                continue  # single-molecule PDB passes through
            molecules = self._molecules(ligand)
            if len(molecules) <= 1:
                continue
            if ligand.source_mol_index is not None:
                out = ctx.register_output(
                    "ligand_mol:%s" % ligand.name,
                    "%s.%s" % (ligand.name, fmt),
                )
                targets.append((ligand, int(ligand.source_mol_index), out))
                continue
            if not ligand.split:
                raise StepError(
                    "配体 %s 的文件包含 %d 个分子但配置了 split: false，"
                    "无法自动拆分；文件中分子: %s"
                    % (
                        ligand.name,
                        len(molecules),
                        ligsplit.names_for_message(molecules),
                    )
                )
            names = ligand.names
            if names is None:
                raise StepError(
                    "配体 %s 的文件包含 %d 个分子（%s），配置未匹配；"
                    "请补充/修正 systems.yaml 的 names 后 ctl retry"
                    % (ligand.name, len(molecules), ligsplit.names_for_message(molecules))
                )
            pin = self._pin_for(ctx, ligand, molecules)
            status, result = ligsplit.match_assignments(names, molecules, pin)
            if status == "mismatch":
                raise StepError(
                    "配体 %s 的 names 未匹配文件分子: %s；文件中分子: %s"
                    % (
                        ligand.name,
                        result,
                        ligsplit.names_for_message(molecules),
                    )
                )
            if status == "ambiguous":
                name, candidates = result
                raise ChoiceError(
                    "配体 %s 存在同名分子，请选择 %s 对应文件中的哪个分子"
                    % (ligand.name, name),
                    question="配体 %s 的 %s 对应文件中的哪个分子？"
                    % (ligand.name, name),
                    candidates=candidates,
                )
            assignments = result
            for nm, idx in assignments:
                if len(nm) > 5:
                    raise StepError(
                        "配体 %s 拆分出的分子名 %s 超过 5 字符，"
                        "请修改文件内的分子名后再试" % (ligand.name, nm)
                    )
            self._expand_in_place(ctx, ligand, assignments)
            for nm, idx in assignments:
                out = ctx.register_output(
                    "ligand_mol:%s" % nm,
                    "%s.%s" % (nm, fmt),
                )
                targets.append((ligand, idx, out))
        return targets

    def _expand_in_place(self, ctx, ligand: Ligand, assignments) -> None:
        """Replace one unexpanded multi-molecule ligand with per-molecule ones."""
        new_ligands = []
        existing = {l.name for l in ctx.system.ligands}
        for nm, idx in assignments:
            if nm in existing:
                raise StepError(
                    "拆分出的配体名 %s 与体系中其他配体重名，请改名" % nm
                )
            data = dict(ligand.raw)
            data["name"] = nm
            data["file"] = ligand.file
            data["split"] = False
            data.pop("names", None)
            data["_source_mol_index"] = idx
            new = Ligand(data, os.path.dirname(ligand.file))
            new.finalize_names(nm)
            new_ligands.append(new)
            existing.add(nm)
        pos = ctx.system.ligands.index(ligand)
        ctx.system.ligands[pos : pos + 1] = new_ligands
        ctx.log.info(
            "配体 %s 已按选择拆分为 %d 个分子: %s",
            ligand.name,
            len(new_ligands),
            ", ".join(l.name for l in new_ligands),
        )

    def run(self, ctx) -> None:
        system = ctx.system
        if system.complex is not None:
            ctx.log.info(
                "复合物体系配体已由 split_complex 拆分，%s 跳过", self.name
            )
            return
        self._check_allowed(ctx)
        self._execute(ctx, self._plan_targets(ctx))

    def _execute(self, ctx, targets) -> None:
        raise NotImplementedError

    def build_commands(self, ctx):
        if ctx.system.complex is not None:
            return []
        self._check_allowed(ctx)
        return self._preview_commands(ctx, self._plan_targets(ctx))

    def _preview_commands(self, ctx, targets):
        return []
