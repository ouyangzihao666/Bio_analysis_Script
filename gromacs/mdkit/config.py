"""Loading and validation of workflow.yaml and systems.yaml."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import yaml

from mdkit.exceptions import ConfigError
from mdkit import mol2 as mol2_mod
from mdkit import sdf as sdf_mod
from mdkit import gro as gro_mod


def load_yaml(path: str) -> dict:
    """Load a YAML file, raising ConfigError on failure."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ConfigError("YAML 文件不存在: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError("YAML 解析失败 %s: %s" % (path, exc))
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("YAML 顶层必须是映射: %s" % path)
    return data


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ConfigError(message)


class StepSpec:
    """One entry of a workflow's ``steps`` list."""

    def __init__(self, index: int, raw: dict, base_dir: str):
        if not isinstance(raw, dict):
            raise ConfigError("steps[%d] 必须是映射" % index)
        name = raw.get("step")
        _require(isinstance(name, str) and name, "steps[%d] 缺少 step 名称" % index)
        self.name: str = name
        self.index: int = index
        self.base_dir: str = base_dir
        self.dir: Optional[str] = raw.get("dir")
        params = raw.get("params", {})
        _require(isinstance(params, dict), "steps[%d].params 必须是映射" % index)
        self.params: Dict[str, Any] = dict(params)
        self.raw = raw

    @property
    def key(self) -> str:
        return self.name


class WorkflowConfig:
    """Parsed and validated workflow.yaml."""

    def __init__(self, data: dict, path: str):
        self.path = os.path.abspath(path)
        self.base_dir = os.path.dirname(self.path)
        self.name: str = str(data.get("name", os.path.basename(self.path)))
        self.failure_policy: str = str(data.get("failure_policy", "continue"))
        self.layout: str = str(data.get("layout", "per_step"))
        self.stage_name: str = str(data.get("stage_name", ".stage"))
        _require(
            self.failure_policy in ("continue", "stop"),
            "failure_policy 必须是 continue 或 stop",
        )
        _require(
            self.layout in ("per_step", "flat"),
            "layout 必须是 per_step 或 flat",
        )
        _require(
            self.stage_name and "/" not in self.stage_name and "\\" not in self.stage_name,
            "stage_name 必须是简单的目录名（不含路径分隔符）",
        )
        raw_steps = data.get("steps")
        _require(isinstance(raw_steps, list) and raw_steps, "steps 必须是非空列表")
        self.steps: List[StepSpec] = [
            StepSpec(i, s, self.base_dir) for i, s in enumerate(raw_steps)
        ]
        self.defaults: Dict[str, Any] = data.get("defaults", {}) or {}
        _require(isinstance(self.defaults, dict), "defaults 必须是映射")
        self.mdp_dir: Optional[str] = data.get("mdp_dir")
        self.steps_dir: Optional[str] = data.get("steps_dir")
        self.dirs: Dict[str, str] = data.get("dirs", {}) or {}
        _require(isinstance(self.dirs, dict), "dirs 必须是映射")
        self.suffixes: Dict[str, str] = data.get("suffixes", {}) or {}
        _require(isinstance(self.suffixes, dict), "suffixes 必须是映射")

    def resolve_mdp_dir(self, builtin_dir: str) -> str:
        if self.mdp_dir:
            p = os.path.expanduser(self.mdp_dir)
            if not os.path.isabs(p):
                p = os.path.join(self.base_dir, p)
            return os.path.abspath(p)
        return os.path.abspath(builtin_dir)

    def resolve_steps_dir(self) -> Optional[str]:
        if not self.steps_dir:
            return None
        p = os.path.expanduser(self.steps_dir)
        if not os.path.isabs(p):
            p = os.path.join(self.base_dir, p)
        return os.path.abspath(p)

    def step_names(self) -> List[str]:
        return [s.name for s in self.steps]

    def step_by_name(self, name: str) -> Optional[StepSpec]:
        for s in self.steps:
            if s.name == name:
                return s
        return None


class Ligand:
    def __init__(self, data: dict, base_dir: str):
        if not isinstance(data, dict):
            raise ConfigError("配体条目必须是映射")
        file_raw = data.get("file")
        _require(isinstance(file_raw, str) and file_raw, "配体缺少 file")
        self.file = _resolve_path(file_raw, base_dir)
        self.name = str(data.get("name", "")).strip() or os.path.splitext(
            os.path.basename(self.file)
        )[0]
        _require(self.name, "配体缺少 name（文件 %s 无法派生名称）" % self.file)
        self.charge = _to_number(data.get("charge", 0), "配体 %s charge" % self.name)
        if "count" in data:
            _require(
                int(data["count"]) == 1,
                "配体 %s count 多拷贝功能已移除，请删除 count 字段；"
                "多拷贝只支持由复合物拆分（同名配体）推导" % self.name,
            )
        self.method = str(data.get("method", "auto"))
        _require(self.method in ("auto", "manual"), "配体 %s method 必须是 auto 或 manual" % self.name)
        fmt = str(data.get("format", "auto"))
        _require(fmt in ("auto", "sdf", "mol2", "pdb"), "配体 %s format 必须是 auto/sdf/mol2/pdb" % self.name)
        self.format = fmt
        names = data.get("names")
        if names is not None:
            _require(isinstance(names, list) and names, "配体 %s names 必须是列表" % self.name)
            _require(all(isinstance(n, str) and n for n in names), "配体 %s names 必须是非空字符串" % self.name)
        self.names = names
        self.split = bool(data.get("split", True))
        if data.get("residue") is not None:
            _require(
                False,
                "配体 %s 的 residue 字段已移除：复合物内嵌配体请改用系统的 complex 块"
                % self.name,
            )
        self.itp_file = _resolve_opt(data.get("itp_file"), base_dir)
        self.gro_file = _resolve_opt(data.get("gro_file"), base_dir)
        if self.method == "manual":
            _require(
                self.itp_file and self.gro_file,
                "配体 %s 手动模式需要 itp_file 和 gro_file" % self.name,
            )
        self.raw = data
        self.source_mol_index = data.get("_source_mol_index")
        self.gmx_name = ""
        self.from_complex = False

    def finalize_names(self, gmx_name: str = "") -> None:
        """Assign the GROMACS-facing molecule name (≤5 chars)."""
        self.gmx_name = gmx_name or self.name
        _require(
            len(self.gmx_name) <= 5,
            "配体名 %s 长度不能超过 5 字符（gro/itp 分子名一致要求）" % self.gmx_name,
        )

    def resolved_format(self) -> str:
        if self.format != "auto":
            return self.format
        ext = os.path.splitext(self.file)[1].lower()
        if ext == ".mol2":
            return "mol2"
        if ext in (".sdf", ".sd"):
            return "sdf"
        if ext == ".pdb":
            return "pdb"
        return "sdf"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "file": self.file,
            "charge": self.charge,
            "gmx_name": self.gmx_name,
            "method": self.method,
            "format": self.format,
            "split": self.split,
        }


class Protein:
    def __init__(self, data: dict, base_dir: str):
        if not isinstance(data, dict):
            raise ConfigError("protein 条目必须是映射")
        file_raw = data.get("file")
        chains_raw = data.get("chains")
        has_file = isinstance(file_raw, str) and file_raw
        has_chains = isinstance(chains_raw, list) and len(chains_raw) > 0
        _require(has_file != has_chains, "protein 必须且只能提供 file 或 chains 之一")
        if has_file:
            self.chains: List[str] = [_resolve_path(file_raw, base_dir)]
        else:
            self.chains = [_resolve_path(c, base_dir) for c in chains_raw]
        self.raw = data

    @property
    def is_multimer(self) -> bool:
        return len(self.chains) > 1


class System:
    def __init__(self, data: dict, base_dir: str, index: int):
        if not isinstance(data, dict):
            raise ConfigError("systems[%d] 必须是映射" % index)
        self.name = str(data.get("name", "")).strip()
        _require(self.name, "systems[%d] 缺少 name" % index)
        _require(
            "/" not in self.name and "\\" not in self.name and self.name != "..",
            "体系名不能包含路径分隔符: %s" % self.name,
        )
        self.complex_raw = data.get("complex")
        has_complex = self.complex_raw is not None
        has_protein = data.get("protein") is not None
        _require(
            has_complex != has_protein,
            "体系 %s 的 protein 与 complex 必须且只能提供其一" % self.name,
        )
        self.slot: Optional[int] = None
        self.review_notes: List[str] = []
        if has_complex:
            self.complex = _parse_complex(self.complex_raw, base_dir)
            self.protein = None
            self.ligands = _build_complex_ligands(self.complex, base_dir)
        else:
            self.complex = None
            self.protein = Protein(data.get("protein", {}), base_dir)
            raw_ligands = data.get("ligands", []) or []
            _require(
                isinstance(raw_ligands, list), "体系 %s ligands 必须是列表" % self.name
            )
            self.ligands: List[Ligand] = [Ligand(l, base_dir) for l in raw_ligands]
        names = [l.name for l in self.ligands]
        _require(len(set(names)) == len(names), "体系 %s 配体名重复" % self.name)
        self.overrides: Dict[str, Dict[str, Any]] = data.get("overrides", {}) or {}
        _require(isinstance(self.overrides, dict), "体系 %s overrides 必须是映射" % self.name)
        for k, v in self.overrides.items():
            _require(isinstance(v, dict), "体系 %s overrides[%s] 必须是映射" % (self.name, k))
        slot = data.get("slot")
        if slot is not None:
            _require(
                isinstance(slot, int) and not isinstance(slot, bool) and slot >= 0,
                "体系 %s slot 必须是非负整数" % self.name,
            )
        self.slot: Optional[int] = slot
        if not has_complex:
            self.ligands = _expand_multi_ligand(
                self.ligands, base_dir, self.review_notes
            )
        for ligand in self.ligands:
            if not ligand.gmx_name:
                ligand.finalize_names()
        names = [l.name for l in self.ligands]
        _require(len(set(names)) == len(names), "体系 %s 配体名重复" % self.name)
        gmx_groups = {}
        for l in self.ligands:
            gmx_groups.setdefault(l.gmx_name, []).append(l)
        for gmx, grp in gmx_groups.items():
            if len(grp) == 1:
                continue
            for l in grp:
                _require(
                    l.from_complex and l.name.startswith(gmx + "_"),
                    "体系 %s 存在映射到同一 GROMACS 分子名 %s 的不同配体"
                    % (self.name, gmx),
                )
        self.raw = data

    @property
    def has_ligands(self) -> bool:
        return len(self.ligands) > 0

    def as_dict(self) -> dict:
        if self.complex is not None:
            return {
                "name": self.name,
                "complex": {
                    "file": self.complex["file"],
                    "ligands": self.complex["ligands"],
                },
                "overrides": self.overrides,
                "slot": self.slot,
            }
        return {
            "name": self.name,
            "protein": {"file": self.protein.chains[0] if not self.protein.is_multimer else None,
                        "chains": self.protein.chains if self.protein.is_multimer else None},
            "ligands": [l.as_dict() for l in self.ligands],
            "overrides": self.overrides,
            "slot": self.slot,
        }


class SystemsConfig:
    """Parsed and validated systems.yaml."""

    def __init__(self, data: dict, path: str):
        self.path = os.path.abspath(path)
        self.base_dir = os.path.dirname(self.path)
        self.work_dir: Optional[str] = data.get("work_dir")
        self.slots: List[Dict[str, Any]] = _parse_slots(data.get("slots"))
        raw_concurrency = data.get("concurrency")
        if raw_concurrency is None:
            self.concurrency = len(self.slots) if self.slots else 1
        else:
            _require(
                isinstance(raw_concurrency, int)
                and not isinstance(raw_concurrency, bool)
                and raw_concurrency >= 1,
                "concurrency 必须是不小于 1 的整数",
            )
            self.concurrency = raw_concurrency
        raw_systems = data.get("systems")
        _require(isinstance(raw_systems, list) and raw_systems, "systems 必须是非空列表")
        self.systems: List[System] = [
            System(s, self.base_dir, i) for i, s in enumerate(raw_systems)
        ]
        names = [s.name for s in self.systems]
        _require(len(set(names)) == len(names), "体系名重复")
        slot_keys = {s["index"] for s in self.slots}
        for system in self.systems:
            if system.slot is not None and system.slot not in slot_keys:
                raise ConfigError(
                    "体系 %s 绑定的槽位 %s 不存在；可用槽位: %s"
                    % (system.name, system.slot, sorted(slot_keys) or "（缺省单槽位 0）")
                )

    def template_args(self, index: int) -> str:
        """Return the mdrun args string for a slot template index."""
        for s in self.slots:
            if s["index"] == index:
                return s["args"]
        raise ConfigError("槽位不存在: %s" % index)

    def slot_exists(self, index: int) -> bool:
        return any(s["index"] == index for s in self.slots)

    def resolve_work_dir(self, cli_value: Optional[str] = None) -> str:
        if cli_value:
            return os.path.abspath(os.path.expanduser(cli_value))
        if self.work_dir:
            p = os.path.expanduser(self.work_dir)
            if not os.path.isabs(p):
                p = os.path.join(self.base_dir, p)
            return os.path.abspath(p)
        raise ConfigError(
            "未指定工作目录：请在 systems.yaml 设置 work_dir 或使用 --work-dir"
        )

    def system_by_name(self, name: str) -> Optional[System]:
        for s in self.systems:
            if s.name == name:
                return s
        return None


def _parse_slots(raw) -> List[Dict[str, Any]]:
    """Parse the top-level ``slots`` map: ``{0: "-ntmpi 1 ...", 1: "..."}``."""
    if raw is None:
        return [{"index": 0, "args": ""}]
    _require(isinstance(raw, dict), "slots 必须是映射（数字键:mdrun 参数串）")
    out = []
    for key, value in raw.items():
        _require(
            isinstance(key, int) and not isinstance(key, bool) and key >= 0,
            "slots 的键必须是非负整数: %r" % (key,),
        )
        _require(isinstance(value, str), "slots[%s] 的值必须是字符串（mdrun 参数串）" % key)
        out.append({"index": key, "args": value})
    out.sort(key=lambda s: s["index"])
    return out


def _resolve_path(value: str, base_dir: str) -> str:
    p = os.path.expanduser(str(value))
    if not os.path.isabs(p):
        p = os.path.join(base_dir, p)
    return os.path.abspath(p)


def _resolve_opt(value, base_dir: str) -> Optional[str]:
    if value is None or value == "":
        return None
    return _resolve_path(str(value), base_dir)


def _to_number(value, label: str):
    if isinstance(value, bool):
        raise ConfigError("%s 不能是布尔值" % label)
    try:
        if isinstance(value, int):
            return float(value) if "." in str(value) else value
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError("%s 必须是数字: %r" % (label, value))


def _expand_multi_ligand(
    ligands: List[Ligand], base_dir: str, notes: List[str]
) -> List[Ligand]:
    """Expand multi-molecule mol2/sdf files into one Ligand per molecule.

    Expansion only happens when the configured ``names`` match the file's
    molecules exactly (same count, every name present, no name requested
    fewer times than it appears). Anything else is left untouched so the
    ligand-splitting step can report the actual molecule names and pause
    (or ask the user to pick among same-name candidates).
    """
    expanded: List[Ligand] = []
    for ligand in ligands:
        if (
            ligand.method != "auto"
            or not ligand.split
            or ligand.resolved_format() not in ("mol2", "sdf")
        ):
            expanded.append(ligand)
            continue
        try:
            if ligand.resolved_format() == "mol2":
                molecules = mol2_mod.parse_molecules(ligand.file)
                for m in molecules:
                    # 与拆分步骤一致：用 substructure 名（FME0 -> FME）匹配
                    m["name"] = mol2_mod.molecule_name(m)
            else:
                molecules = sdf_mod.parse_molecules(ligand.file)
        except ConfigError:
            # 文件缺失/不可读：留待拆分步骤在运行期清晰报错，配置加载不阻塞
            notes.append(
                "配体 %s 的文件暂时无法解析（%s），将在拆分步骤检查"
                % (ligand.name, ligand.file)
            )
            expanded.append(ligand)
            continue
        if len(molecules) <= 1:
            expanded.append(ligand)
            continue
        if ligand.names is None:
            notes.append(
                "配体 %s 的文件包含 %d 个分子，未提供 names，"
                "将在拆分步骤报错并列出分子名" % (ligand.name, len(molecules))
            )
            expanded.append(ligand)
            continue
        names = list(ligand.names)
        supply = {}
        for m in molecules:
            supply[m["name"]] = supply.get(m["name"], 0) + 1
        demand = {}
        for n in names:
            demand[n] = demand.get(n, 0) + 1
        if any(supply.get(n, 0) < demand.get(n, 0) for n in demand):
            notes.append(
                "配体 %s 的 names 与文件分子名不匹配，将在拆分步骤报错" % ligand.name
            )
            expanded.append(ligand)
            continue
        if any(supply.get(n, 0) > demand.get(n, 0) for n in demand):
            notes.append(
                "配体 %s 存在同名分子（候选多于需求），"
                "拆分步骤将等待选择" % ligand.name
            )
            expanded.append(ligand)
            continue
        used = set()
        for nm in names:
            for i, molecule in enumerate(molecules):
                if molecule["name"] != nm or i in used:
                    continue
                used.add(i)
                data = dict(ligand.raw)
                data["name"] = nm
                data["split"] = False
                data.pop("names", None)
                data["_source_mol_index"] = molecule["index"]
                new_ligand = Ligand(data, base_dir)
                new_ligand.finalize_names(nm)
                expanded.append(new_ligand)
                break
        ignored = [
            m["name"] for i, m in enumerate(molecules) if i not in used
        ]
        if ignored:
            notes.append(
                "配体 %s 的文件中另有 %d 个分子未在 names 中（%s），将被忽略"
                % (ligand.name, len(ignored), "、".join(sorted(set(ignored))))
            )
    return expanded


def _parse_complex(raw, base_dir: str) -> dict:
    """Validate the top-level ``complex`` block."""
    _require(isinstance(raw, dict), "complex 必须是映射")
    file_raw = raw.get("file")
    _require(isinstance(file_raw, str) and file_raw, "complex 缺少 file")
    raw_ligands = raw.get("ligands")
    _require(
        isinstance(raw_ligands, list) and raw_ligands,
        "complex 必须提供非空 ligands 列表",
    )
    ligands = []
    for i, item in enumerate(raw_ligands):
        _require(isinstance(item, dict), "complex.ligands[%d] 必须是映射" % i)
        name = str(item.get("name", "")).strip()
        _require(name, "complex.ligands[%d] 缺少 name" % i)
        _require(1 <= len(name) <= 5, "complex 配体名 %s 必须是 1-5 字符" % name)
        rec = {"name": name, "charge": _to_number(item.get("charge", 0), name)}
        if item.get("chain") is not None:
            chain = str(item["chain"]).strip()
            _require(len(chain) == 1, "complex 配体 %s chain 必须为单个字符" % name)
            rec["chain"] = chain
        else:
            rec["chain"] = ""
        ligands.append(rec)
    return {"file": _resolve_path(file_raw, base_dir), "ligands": ligands}


def _build_complex_ligands(complex_cfg: dict, base_dir: str) -> List[Ligand]:
    """Synthesize Ligand objects from the complex PDB + complex.ligands.

    Same-name ligands are matched to PDB residues by (chain, resid) order
    and get a resid-suffixed display name (``UNK_501``) while sharing the
    configured ``gmx_name``. Count, charge and atom-count mismatches are
    rejected at configuration time so downstream atom-count invariants hold.
    """
    scan = gro_mod.scan_pdb_residues(complex_cfg["file"])
    models = scan.pop("models", 0)
    if models > 1:
        raise ConfigError(
            "复合物 PDB 包含 %d 个 MODEL（多分子 PDB 不支持）：%s"
            % (models, complex_cfg["file"])
        )
    entries = list(complex_cfg["ligands"])
    # Group entries by (name, chain) preserving order.
    groups = {}
    order = []
    for e in entries:
        key = (e["name"], e["chain"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(e)
    ligands: List[Ligand] = []
    for key in order:
        name, chain = key
        group = groups[key]
        recs = [
            r
            for r in scan.get(name, [])
            if (not chain) or r["chain"] == chain
        ]
        if len(recs) != len(group):
            found = "、".join(
                "%s%s%d" % (r["chain"], "" if not r["chain"] else ":", r["resid"])
                for r in recs
            ) or "（无）"
            raise ConfigError(
                "复合物中 %s 的条目数（%d）与 PDB 残基数（%d）不一致；"
                "实际存在: %s（%s）"
                % (name, len(group), len(recs), found, complex_cfg["file"])
            )
        charges = {e["charge"] for e in group}
        _require(
            len(charges) == 1,
            "复合物同名配体 %s 的 charge 必须一致（当前: %s）"
            % (name, sorted(charges)),
        )
        atom_counts = {r["natoms"] for r in recs}
        _require(
            len(atom_counts) == 1,
            "复合物同名配体 %s 的原子数不一致（%s），"
            "说明它们是不同分子，请改用不同名称"
            % (name, sorted(atom_counts)),
        )
        charge = group[0]["charge"]
        for e, rec in zip(group, recs):
            data = {
                "file": complex_cfg["file"],
                "name": e["name"],
                "charge": charge,
            }
            if len(group) > 1:
                data["name"] = "%s_%d" % (e["name"], rec["resid"])
            lig = Ligand(data, base_dir)
            lig.finalize_names(e["name"])
            lig.from_complex = True
            lig.resid = rec["resid"]
            lig.chain = rec["chain"]
            ligands.append(lig)
    return ligands


def load_workflow(path: str) -> WorkflowConfig:
    return WorkflowConfig(load_yaml(path), path)


def load_systems(path: str) -> SystemsConfig:
    return SystemsConfig(load_yaml(path), path)
