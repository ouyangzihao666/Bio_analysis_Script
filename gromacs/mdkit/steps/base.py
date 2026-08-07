"""Step base class and the StepContext injected into every step."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from mdkit import mdp as mdp_mod
from mdkit.exceptions import ConfigError, StepError


class Step:
    """Interface every mdkit step must implement.

    Subclasses declare:
      name            unique step name
      version         step implementation version (part of signatures)
      description     human-readable description
      inputs          logical input names
      outputs         list of (logical_name, filename_template, optional)
      param_schema    parameter name -> {type, default, choices}
      env_requirements  tools required by this step (checked by ``doctor``)

    ``run(ctx)`` performs the work. Steps must never ``os.chdir`` or mutate
    global state; all file writes happen inside ``ctx.cwd`` (the stage dir).
    """

    name: str = ""
    version: str = "1.0"
    description: str = ""
    inputs: List[str] = []
    outputs: List[tuple] = []
    param_schema: Dict[str, dict] = {}
    env_requirements: List[str] = []

    def validate_params(self, params: dict) -> dict:
        """Merge defaults, type-check, reject unknown parameters."""
        merged = {}
        for pname, spec in self.param_schema.items():
            if pname in params:
                merged[pname] = self._coerce(pname, params[pname], spec)
            elif "default" in spec:
                merged[pname] = spec["default"]
            else:
                raise ConfigError("步骤 %s 缺少必要参数: %s" % (self.name, pname))
        for pname in params:
            if pname not in self.param_schema:
                raise ConfigError("步骤 %s 未知参数: %s" % (self.name, pname))
        return merged

    def resolve_inputs(self, system) -> list:
        """Logical input names this step will consume (may be system-specific)."""
        return list(self.inputs)

    def _coerce(self, pname, value, spec):
        if value is None and spec.get("default") is None:
            return None
        ptype = getattr(spec.get("type"), "__name__", str(spec.get("type")))
        if ptype == "str" and not isinstance(value, str):
            value = str(value)
        if ptype == "int":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ConfigError("步骤 %s 参数 %s 必须是整数" % (self.name, pname))
        elif ptype == "float":
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ConfigError("步骤 %s 参数 %s 必须是数字" % (self.name, pname))
        elif ptype == "bool":
            if isinstance(value, bool):
                pass
            elif isinstance(value, str):
                value = value.lower() in ("true", "yes", "1", "on")
            else:
                raise ConfigError("步骤 %s 参数 %s 必须是布尔值" % (self.name, pname))
        elif ptype == "list":
            if isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
            if not isinstance(value, list):
                raise ConfigError("步骤 %s 参数 %s 必须是列表" % (self.name, pname))
        elif ptype == "dict":
            if not isinstance(value, dict):
                raise ConfigError("步骤 %s 参数 %s 必须是映射" % (self.name, pname))
        elif ptype == "path":
            value = os.path.abspath(os.path.expanduser(str(value)))
        choices = spec.get("choices")
        if choices and value not in choices:
            raise ConfigError(
                "步骤 %s 参数 %s 取值必须为 %s" % (self.name, pname, choices)
            )
        return value

    def preview(self, ctx: "StepContext") -> None:
        """Dry-run preview; prints what the step would do."""
        ctx.log.info(
            "[preview] %s: dir=%s params=%s",
            self.name,
            ctx.step_dir,
            ctx.params,
        )

    def run(self, ctx: "StepContext") -> None:
        raise NotImplementedError("%s.run() 未实现" % self.name)


class StepContext:
    """Runtime context handed to every step."""

    def __init__(
        self,
        *,
        system,
        step: Step,
        params: dict,
        step_dir: str,
        cwd: str,
        registry,
        cmd,
        log,
        mdp_dir: str,
        dry_run: bool = False,
        force: bool = False,
        run_dir: str = "",
    ):
        self.system = system
        self.step = step
        self.params = params
        self.step_dir = os.path.abspath(step_dir)
        self.cwd = os.path.abspath(cwd)
        self.registry = registry
        self.cmd = cmd
        self.log = log
        self.mdp_dir = mdp_dir
        self.dry_run = dry_run
        self.force = force
        self.run_dir = os.path.abspath(run_dir)
        self._outputs: Dict[str, str] = {}
        self._optional: Dict[str, bool] = {}
        self.commands: List[str] = []

    # -- files -----------------------------------------------------------
    def path(self, rel: str) -> str:
        return os.path.join(self.cwd, rel)

    def register_output(self, logical: str, rel: str, optional: bool = False) -> str:
        if os.path.isabs(rel):
            rel = os.path.relpath(rel, self.cwd)
        self._outputs[logical] = rel
        self._optional[logical] = bool(optional)
        return self.path(rel)

    def get_input(self, logical: str) -> str:
        return self.registry.require(logical, for_step=self.step.name)

    def copy_input(self, logical: str, rel: str) -> str:
        src = self.get_input(logical)
        dst = self.path(rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, "rb") as fh_in, open(dst, "wb") as fh_out:
            fh_out.write(fh_in.read())
        return dst

    def remove_temp(self, rel: str) -> None:
        target = os.path.abspath(self.path(rel))
        if not target.startswith(self.cwd + os.sep):
            raise StepError("禁止删除工作目录之外的文件: %s" % target)
        if os.path.isfile(target):
            os.remove(target)
        elif os.path.isdir(target):
            import shutil

            shutil.rmtree(target)

    # -- commands --------------------------------------------------------
    def run_gmx(self, args: List[str], stdin_text: Optional[str] = None, timeout=None):
        self.commands.append("gmx " + " ".join(args))
        return self.cmd.run_gmx(args, stdin_text=stdin_text, cwd=self.cwd, timeout=timeout)

    def run_cmd(self, argv: List[str], stdin_text: Optional[str] = None, timeout=None):
        self.commands.append(" ".join(argv))
        return self.cmd.run(argv, stdin_text=stdin_text, cwd=self.cwd, timeout=timeout)

    # -- mdp -------------------------------------------------------------
    def render_mdp(self, mdp_spec: str, overrides: Optional[dict], rel: str):
        template = mdp_mod.resolve_template(mdp_spec, self.mdp_dir)
        info = mdp_mod.render_mdp(template, overrides, self.path(rel))
        return self.path(rel), info

    # -- outputs ---------------------------------------------------------
    def outputs_map(self):
        return dict(self._outputs), dict(self._optional)
