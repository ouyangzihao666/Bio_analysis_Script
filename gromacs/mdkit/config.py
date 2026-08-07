"""Loading and validation of workflow.yaml and systems.yaml."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import yaml

from mdkit.exceptions import ConfigError


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
        _require(
            self.failure_policy in ("continue", "stop"),
            "failure_policy 必须是 continue 或 stop",
        )
        _require(
            self.layout in ("per_step", "flat"),
            "layout 必须是 per_step 或 flat",
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
        self.name = str(data.get("name", "")).strip()
        _require(self.name, "配体缺少 name")
        file_raw = data.get("file")
        _require(isinstance(file_raw, str) and file_raw, "配体 %s 缺少 file" % self.name)
        self.file = _resolve_path(file_raw, base_dir)
        self.charge = _to_number(data.get("charge", 0), "配体 %s charge" % self.name)
        self.count = int(data.get("count", 1))
        _require(self.count >= 1, "配体 %s count 必须 >= 1" % self.name)
        self.method = str(data.get("method", "auto"))
        _require(self.method in ("auto", "manual"), "配体 %s method 必须是 auto 或 manual" % self.name)
        self.itp_file = _resolve_opt(data.get("itp_file"), base_dir)
        self.gro_file = _resolve_opt(data.get("gro_file"), base_dir)
        if self.method == "manual":
            _require(
                self.itp_file and self.gro_file,
                "配体 %s 手动模式需要 itp_file 和 gro_file" % self.name,
            )
        self.raw = data

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "file": self.file,
            "charge": self.charge,
            "count": self.count,
            "method": self.method,
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
        self.protein = Protein(data.get("protein", {}), base_dir)
        raw_ligands = data.get("ligands", []) or []
        _require(isinstance(raw_ligands, list), "体系 %s ligands 必须是列表" % self.name)
        self.ligands: List[Ligand] = [Ligand(l, base_dir) for l in raw_ligands]
        names = [l.name for l in self.ligands]
        _require(len(set(names)) == len(names), "体系 %s 配体名重复" % self.name)
        self.overrides: Dict[str, Dict[str, Any]] = data.get("overrides", {}) or {}
        _require(isinstance(self.overrides, dict), "体系 %s overrides 必须是映射" % self.name)
        for k, v in self.overrides.items():
            _require(isinstance(v, dict), "体系 %s overrides[%s] 必须是映射" % (self.name, k))
        self.raw = data

    @property
    def has_ligands(self) -> bool:
        return len(self.ligands) > 0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "protein": {"file": self.protein.chains[0] if not self.protein.is_multimer else None,
                        "chains": self.protein.chains if self.protein.is_multimer else None},
            "ligands": [l.as_dict() for l in self.ligands],
            "overrides": self.overrides,
        }


class SystemsConfig:
    """Parsed and validated systems.yaml."""

    def __init__(self, data: dict, path: str):
        self.path = os.path.abspath(path)
        self.base_dir = os.path.dirname(self.path)
        self.work_dir: Optional[str] = data.get("work_dir")
        raw_systems = data.get("systems")
        _require(isinstance(raw_systems, list) and raw_systems, "systems 必须是非空列表")
        self.systems: List[System] = [
            System(s, self.base_dir, i) for i, s in enumerate(raw_systems)
        ]
        names = [s.name for s in self.systems]
        _require(len(set(names)) == len(names), "体系名重复")

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


def load_workflow(path: str) -> WorkflowConfig:
    return WorkflowConfig(load_yaml(path), path)


def load_systems(path: str) -> SystemsConfig:
    return SystemsConfig(load_yaml(path), path)
