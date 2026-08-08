"""Run orchestration: state machine, signatures, intervention, rollback."""

from __future__ import annotations

import os
import logging
import shutil
import signal
import time
from typing import Dict, List, Optional

from mdkit.exceptions import ConfigError, InputError, MdkitError, RunError, StepError
from mdkit.gmx import CommandRunner
from mdkit.monitor import RunLock, RunState, load_or_init_status
from mdkit.registry import FileRegistry, conventions_with_dirs
from mdkit.signature import sha256_file, step_signature
from mdkit.steps import load_steps
from mdkit.steps.base import StepContext
from mdkit.transaction import Transaction


class Runner:
    def __init__(
        self,
        workflow,
        systems_cfg,
        work_dir: str,
        system_filter: Optional[List[str]] = None,
        from_step: Optional[str] = None,
        force: bool = False,
        timeout: Optional[float] = None,
        log=None,
    ):
        self.workflow = workflow
        self.systems_cfg = systems_cfg
        self.work_dir = os.path.abspath(work_dir)
        self.system_filter = system_filter
        self.from_step = from_step
        self.force = force
        self.timeout = timeout
        self.log = log if log is not None else logging.getLogger("mdkit")
        self.steps = load_steps(workflow.resolve_steps_dir())
        self._validate_workflow_steps()
        self.cmd = CommandRunner(log, dry_run=False, timeout=timeout)
        self._interrupted = False
        self._failed_any = False
        self._stop_all = False
        self.state = None

    # ------------------------------------------------------------------
    def _validate_workflow_steps(self) -> None:
        for spec in self.workflow.steps:
            if spec.name not in self.steps:
                raise ConfigError(
                    "工作流包含未知步骤: %s（可用: %s）"
                    % (spec.name, ", ".join(sorted(self.steps)))
                )
        if self.from_step and self.workflow.step_by_name(self.from_step) is None:
            raise ConfigError("--from 指定的步骤不存在: %s" % self.from_step)
        for system in self.systems_cfg.systems:
            for key in system.overrides:
                if self.workflow.step_by_name(key) is None:
                    # systems.yaml 是共享数据：不属于当前工作流的 overrides 忽略。
                    if self.log:
                        self.log.warning(
                            "体系 %s 的 overrides 包含当前工作流不存在的步骤 %s，已忽略",
                            system.name,
                            key,
                        )

    def _step_dir(self, system_name: str, spec) -> str:
        return step_dir_for(self.workflow, self.work_dir, system_name, spec)

    def _effective_params(self, system, spec) -> dict:
        return effective_params(self.workflow, self.steps, system, spec)

    def _make_ctx(self, system, spec, step, params, step_dir, cwd, registry, mdp_dir):
        return StepContext(
            system=system,
            step=step,
            params=params,
            step_dir=step_dir,
            cwd=cwd,
            registry=registry,
            cmd=self.cmd,
            log=self.log,
            mdp_dir=mdp_dir,
            dry_run=False,
            force=self.force,
            run_dir=self.work_dir,
        )

    def _registry(self, system, data) -> FileRegistry:
        registry = FileRegistry(
            self.work_dir,
            system,
            conventions=conventions_with_dirs(self.workflow.dirs),
        )
        registry.register_source("protein_pdb", system.protein.chains[0])
        for i, chain in enumerate(system.protein.chains):
            registry.register_source("protein_chain:%d" % i, chain)
        for ligand in system.ligands:
            registry.register_source("ligand_sdf:%s" % ligand.name, ligand.file)
            if ligand.method == "manual":
                registry.register_source(
                    "ligand_itp_src:%s" % ligand.name, ligand.itp_file
                )
                registry.register_source(
                    "ligand_gro_src:%s" % ligand.name, ligand.gro_file
                )
        sys_entry = data["systems"].get(system.name, {})
        for spec in self.workflow.steps:
            st = sys_entry.get("steps", {}).get(spec.name, {})
            for logical, rec in (st.get("outputs") or {}).items():
                if os.path.exists(rec.get("path", "")):
                    registry.set(logical, rec["path"], producer=spec.name)
        return registry

    def _input_hashes(self, step, system, registry) -> Dict[str, str]:
        logicals = []
        for logical in step.resolve_inputs(system):
            if logical not in logicals:
                logicals.append(logical)
        hashes = {}
        for logical in logicals:
            path = registry.get(logical)
            if path and os.path.isfile(path):
                hashes[logical] = sha256_file(path)
        return hashes

    def _check_inputs(self, step, system, registry, for_step_dir: str) -> Dict[str, str]:
        """Pre-check declared inputs; raise InputError listing missing files."""
        missing = []
        hashes = {}
        for logical in step.resolve_inputs(system):
            path = registry.get(logical)
            if path is None or not os.path.isfile(path):
                missing.append(logical)
                continue
            hashes[logical] = sha256_file(path)
        if missing:
            raise InputError(
                "步骤 %s 缺少输入: %s" % (step.name, ", ".join(missing)),
                details={"missing": missing},
            )
        return hashes

    # ------------------------------------------------------------------
    def run(self) -> int:
        self._install_signal_handlers()
        os.makedirs(self.work_dir, exist_ok=True)
        lock = RunLock(self.work_dir)
        lock.acquire(timeout=0.0)
        state = RunState(self.work_dir)
        self.state = state
        step_names = self.workflow.step_names()
        try:
            data = load_or_init_status(
                self.work_dir,
                self.workflow.name,
                self.workflow.path,
                self.systems_cfg.path,
                self.systems_cfg.systems,
                step_names,
            )
            self._mark_stale(data)
            selected = self._selected_systems()
            for system in selected:
                if self._interrupted:
                    break
                self._run_system(data, system)
                if self._stop_all:
                    break
            self.state.save(data)
            self._print_summary(data)
        finally:
            lock.release()
        if self._failed_any:
            return 2
        return 0

    def _install_signal_handlers(self) -> None:
        def handler(signum, frame):
            self._interrupted = True
            self.cmd.interrupt()

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def _selected_systems(self) -> list:
        systems = self.systems_cfg.systems
        if not self.system_filter:
            return systems
        result = []
        for name in self.system_filter:
            system = self.systems_cfg.system_by_name(name)
            if system is None:
                raise ConfigError("--system 指定的体系不存在: %s" % name)
            result.append(system)
        return result

    def _mark_stale(self, data) -> None:
        step_names = self.workflow.step_names()
        for system in self.systems_cfg.systems:
            sys_entry = data["systems"].get(system.name)
            if not sys_entry:
                continue
            registry = self._registry(system, data)
            stale_until = None
            for spec in self.workflow.steps:
                st = sys_entry["steps"].get(spec.name, {})
                if stale_until is not None and st.get("status") == "done":
                    st["status"] = "stale"
                    st["note"] = "上游输入或参数已变化，需要重算"
                    continue
                if st.get("status") != "done":
                    continue
                try:
                    step = self.steps[spec.name]
                    params = self._effective_params(system, spec)
                    hashes = self._input_hashes(step, system, registry)
                    current = step_signature(
                        step.name, step.version, params, hashes
                    )
                except Exception:
                    current = None
                if current != st.get("signature"):
                    st["status"] = "stale"
                    st["note"] = "参数或输入已变化，需要重算"
                    stale_until = spec.index
                    continue
                # Rebuild registry from this step's recorded outputs.
                for logical, rec in (st.get("outputs") or {}).items():
                    if os.path.exists(rec.get("path", "")):
                        registry.set(logical, rec["path"], producer=spec.name)

    # ------------------------------------------------------------------
    def _run_system(self, data, system) -> None:
        sys_entry = data["systems"][system.name]
        sys_entry["status"] = "running"
        self.log.info("===== 处理体系: %s =====", system.name)
        for note in getattr(system, "review_notes", []):
            self.log.warning("[%s] ⚠ %s", system.name, note)
        registry = self._registry(system, data)
        start_index = 0
        if self.from_step:
            start_index = self.workflow.step_by_name(self.from_step).index
        for spec in self.workflow.steps[start_index:]:
            if self._interrupted:
                self.log.warning("检测到中断，停止体系 %s", system.name)
                sys_entry["status"] = "interrupted"
                return
            step = self.steps[spec.name]
            params = self._effective_params(system, spec)
            step_dir = self._step_dir(system.name, spec)
            st = sys_entry["steps"][spec.name]

            if step.name == "manual_check" and st.get("status") in ("done", "skipped"):
                self.log.info("[%s] 跳过 %s（已放行）", system.name, spec.name)
                continue
            if step.name == "manual_check" and st.get("status") not in ("done", "skipped"):
                st["status"] = "awaiting_input"
                st["note"] = params.get("message", "等待人工/Codex 确认")
                sys_entry["status"] = "paused"
                self.log.warning(
                    "体系 %s 在 manual_check 暂停：%s", system.name, st["note"]
                )
                self.log.info(
                    "放行: mdkit skip %s %s manual_check --reason '...'",
                    self.work_dir,
                    system.name,
                )
                return

            try:
                input_hashes = self._check_inputs(step, system, registry, step_dir)
            except InputError as exc:
                st["status"] = "failed"
                st["error"] = str(exc)
                st["finished_at"] = _now()
                sys_entry["status"] = "failed"
                self._failed_any = True
                self.log.error("[%s] %s", system.name, exc)
                self._handle_failure(data, sys_entry, spec, st, system, params)
                return

            signature = step_signature(
                step.name, step.version, params, input_hashes
            )
            if (
                not self.force
                and st.get("status") == "done"
                and st.get("signature") == signature
                and self._outputs_present(st)
            ):
                self.log.info(
                    "[%s] 跳过 %s（已完成且签名一致）", system.name, spec.name
                )
                continue
            if st.get("status") == "awaiting_input":
                self.log.info("[%s] %s 等待放行（skip/retry）", system.name, spec.name)
                return

            self.log.info(
                "[%s] 开始步骤 %s（目录: %s）", system.name, spec.name, step_dir
            )
            st["status"] = "running"
            st["started_at"] = _now()
            st["error"] = None
            st["stderr_tail"] = None
            st["note"] = None
            self.state.save(data)

            tx = Transaction(step_dir, stage_name=self.workflow.stage_name)
            stage = tx.begin()
            run_ctx = self._make_ctx(
                system, spec, step, params, step_dir, stage, registry, self.workflow.resolve_mdp_dir(self._builtin_mdp_dir())
            )
            started = time.time()
            try:
                step.run(run_ctx)
                outputs_map, optional_map = run_ctx.outputs_map()
                final = tx.commit(outputs_map, optional_map)
                out_records = {}
                for logical, path in final.items():
                    if os.path.isdir(path):
                        out_records[logical] = {
                            "path": path,
                            "sha256": None,
                            "dir": True,
                        }
                    else:
                        out_records[logical] = {
                            "path": path,
                            "sha256": sha256_file(path),
                        }
                    registry.set(logical, path, producer=spec.name)
                st["status"] = "done"
                st["signature"] = signature
                st["outputs"] = out_records
                st["finished_at"] = _now()
                st["duration_s"] = round(time.time() - started, 1)
                st["commands"] = run_ctx.commands
                self.state.save(data)
                self.log.info("[%s] 步骤 %s 完成（%.1fs）", system.name, spec.name, st["duration_s"])
            except MdkitError as exc:
                tx.abort(keep_stage=True)
                st["status"] = "failed"
                st["error"] = str(exc)
                st["exit_code"] = getattr(exc, "exit_code", None)
                st["stderr_tail"] = getattr(exc, "output_tail", None)
                st["finished_at"] = _now()
                st["duration_s"] = round(time.time() - started, 1)
                self._failed_any = True
                if self._interrupted:
                    st["status"] = "interrupted"
                    sys_entry["status"] = "interrupted"
                    self.log.warning("[%s] 步骤 %s 被中断", system.name, spec.name)
                    return
                self.log.error("[%s] 步骤 %s 失败: %s", system.name, spec.name, exc)
                if run_ctx.commands:
                    self.log.error("  命令: %s", run_ctx.commands[-1])
                if st["stderr_tail"]:
                    self.log.error("  输出尾部:\n%s", st["stderr_tail"][-2000:])
                self.state.save(data)
                self._handle_failure(data, sys_entry, spec, st, system, params)
                return
            except KeyboardInterrupt:
                st["status"] = "interrupted"
                sys_entry["status"] = "interrupted"
                raise
        if sys_entry["status"] == "running":
            sys_entry["status"] = "done"
            self.log.info("===== 体系 %s 完成 =====", system.name)

    def _handle_failure(self, data, sys_entry, spec, st, system, params) -> None:
        on_failure = params.get("on_failure", "auto")
        if on_failure == "pause":
            st["status"] = "awaiting_input"
            st["note"] = "步骤失败，等待人工/Codex 干预"
            sys_entry["status"] = "paused"
            self.log.warning(
                "体系 %s 在 %s 暂停等待干预；"
                "可用 mdkit retry/skip 处理后恢复",
                system.name,
                spec.name,
            )
            return
        if self.workflow.failure_policy == "stop":
            self._stop_all = True
            sys_entry["status"] = "failed"
            self.log.error("failure_policy=stop：整个运行终止")
            return
        # continue: mark remaining steps of this system as skipped.
        sys_entry["status"] = "failed"
        for later in self.workflow.steps[spec.index + 1 :]:
            later_st = sys_entry["steps"][later.name]
            if later_st.get("status") not in ("done",):
                later_st["status"] = "skipped"
                later_st["note"] = "上游步骤 %s 失败，本步骤未执行" % spec.name
        self.log.warning("体系 %s 失败，继续处理其他体系", system.name)

    def _outputs_present(self, st) -> bool:
        outputs = st.get("outputs") or {}
        if not outputs:
            return st.get("status") == "done"
        return all(os.path.exists(rec.get("path", "")) for rec in outputs.values())

    def _builtin_mdp_dir(self) -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(os.path.dirname(here), "configs", "mdp")

    def _print_summary(self, data) -> None:
        total = len(self.systems_cfg.systems)
        done = sum(
            1
            for s in self.systems_cfg.systems
            if data["systems"][s.name].get("status") == "done"
        )
        self.log.info("运行摘要: %d/%d 体系完成", done, total)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
# ----------------------------------------------------------------------
# Status manipulation commands (skip / retry / rollback / clean)
# ----------------------------------------------------------------------


def load_run_data(run_dir: str) -> dict:
    return RunState(run_dir).load()


def find_system_step(run_dir: str, system_name: str, step_name: str):
    data = load_run_data(run_dir)
    if system_name not in data.get("systems", {}):
        raise ConfigError("run 中不存在体系: %s" % system_name)
    sys_entry = data["systems"][system_name]
    if step_name not in sys_entry.get("steps", {}):
        raise ConfigError("体系 %s 中不存在步骤: %s" % (system_name, step_name))
    return data, sys_entry, sys_entry["steps"][step_name]


def cmd_skip(run_dir: str, system_name: str, step_name: str, reason: str = "", outputs=None) -> dict:
    state = RunState(run_dir)
    with _locked(run_dir):
        data = state.load()
        if system_name not in data.get("systems", {}):
            raise ConfigError("run 中不存在体系: %s" % system_name)
        sys_entry = data["systems"][system_name]
        if step_name not in sys_entry.get("steps", {}):
            raise ConfigError("体系 %s 中不存在步骤: %s" % (system_name, step_name))
        st = sys_entry["steps"][step_name]
        st["status"] = "skipped"
        st["note"] = reason or "人工/Codex 跳过"
        out_records = {}
        for item in outputs or []:
            if "=" not in item:
                raise ConfigError("--output 格式应为 logical=path: %s" % item)
            logical, path = item.split("=", 1)
            path = path if os.path.isabs(path) else os.path.join(run_dir, path)
            if not os.path.isfile(path):
                raise ConfigError("--output 文件不存在: %s" % path)
            out_records[logical.strip()] = {"path": os.path.abspath(path), "sha256": sha256_file(path)}
        st["outputs"] = out_records
        st["signature"] = None
        if sys_entry.get("status") in ("paused", "failed"):
            sys_entry["status"] = "running"
        state.save(data)
        return {"system": system_name, "step": step_name, "status": "skipped", "reason": st["note"]}


def cmd_retry(run_dir: str, system_name: str, step_name: Optional[str] = None) -> dict:
    state = RunState(run_dir)
    with _locked(run_dir):
        data = state.load()
        if system_name not in data.get("systems", {}):
            raise ConfigError("run 中不存在体系: %s" % system_name)
        sys_entry = data["systems"][system_name]
        names = list(sys_entry.get("steps", {}).keys())
        target = step_name or names[0]
        if target not in names:
            raise ConfigError("体系 %s 中不存在步骤: %s" % (system_name, target))
        idx = names.index(target)
        for name in names[idx:]:
            st = sys_entry["steps"][name]
            if st.get("status") in ("done",):
                st["status"] = "stale"
                st["note"] = "retry：需要重算"
            elif st.get("status") in (
                "failed",
                "awaiting_input",
                "interrupted",
                "skipped",
            ):
                st["status"] = "pending"
                st["note"] = "retry：重置为待执行"
        if sys_entry.get("status") in ("paused", "failed", "interrupted"):
            sys_entry["status"] = "pending"
        state.save(data)
        return {"system": system_name, "from": target, "reset": names[idx:]}


def cmd_rollback(run_dir: str, system_name: str, step_name: Optional[str] = None) -> dict:
    return cmd_retry(run_dir, system_name, step_name)


def cmd_clean(
    run_dir: str,
    system_name: str,
    from_step: Optional[str] = None,
    yes: bool = False,
) -> dict:
    state = RunState(run_dir)
    with _locked(run_dir):
        data = state.load()
        if system_name not in data.get("systems", {}):
            raise ConfigError("run 中不存在体系: %s" % system_name)
        sys_entry = data["systems"][system_name]
        names = list(sys_entry.get("steps", {}).keys())
        idx = 0
        if from_step:
            if from_step not in names:
                raise ConfigError("体系 %s 中不存在步骤: %s" % (system_name, from_step))
            idx = names.index(from_step)
        removed = []
        dirs = _managed_step_dirs(run_dir, data, system_name)
        if dirs and from_step:
            names_set = set(dirs)
            if from_step not in names_set:
                raise ConfigError("体系 %s 中不存在步骤: %s" % (system_name, from_step))
            trim = False
            for name in list(dirs):
                if name == from_step:
                    trim = True
                if not trim:
                    del dirs[name]
        for name in names[idx:]:
            st = sys_entry["steps"][name]
            paths = [rec.get("path", "") for rec in (st.get("outputs") or {}).values()]
            if name in dirs:
                d = dirs[name]
                if os.path.isdir(d) and _under(run_dir, d):
                    if yes:
                        shutil.rmtree(d)
                        removed.append(d + "/")
                    else:
                        removed.append(d + "/（待确认）")
                    continue
            for p in paths:
                if p and os.path.exists(p) and _under(run_dir, p):
                    if yes and os.path.isdir(p):
                        shutil.rmtree(p)
                        removed.append(p + "/")
                    elif yes:
                        os.remove(p)
                        removed.append(p)
                    else:
                        removed.append(p + "（待确认）")
            st["outputs"] = {}
            st["signature"] = None
            if st.get("status") in ("done", "stale"):
                st["status"] = "pending"
        if sys_entry.get("status") == "done":
            sys_entry["status"] = "pending"
        state.save(data)
        return {"system": system_name, "from": names[idx], "files": removed, "confirmed": yes}


def _managed_step_dirs(run_dir: str, data: dict, system_name: str) -> dict:
    """Recompute managed step dirs from the run's workflow, if loadable."""
    try:
        from mdkit.config import load_workflow
        from mdkit.config import WorkflowConfig

        wf_path = data.get("run", {}).get("workflow")
        if not wf_path or not os.path.isfile(wf_path):
            return {}
        wf = load_workflow(wf_path)
        dirs = {}
        for spec in wf.steps:
            dirs[spec.name] = step_dir_for(wf, run_dir, system_name, spec)
        return dirs
    except Exception:
        return {}


def step_dir_for(workflow, run_dir: str, system_name: str, spec) -> str:
    system_dir = os.path.join(os.path.abspath(run_dir), system_name)
    if workflow.layout == "flat":
        base = system_dir
    else:
        base = os.path.join(
            system_dir, "%02d_%s" % (spec.index + 1, spec.name)
        )
    if spec.dir:
        if "/" in spec.dir or "\\" in spec.dir:
            raise ConfigError("步骤 dir 不能包含路径分隔符: %s" % spec.dir)
        base = os.path.join(system_dir, spec.dir)
    return base


class _locked:
    def __init__(self, run_dir: str):
        self.lock = RunLock(run_dir)

    def __enter__(self):
        self.lock.acquire(timeout=0.0)
        return self

    def __exit__(self, *exc):
        self.lock.release()


def _under(root: str, path: str) -> bool:
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    return path == root or path.startswith(root + os.sep)


def effective_params(workflow, steps, system, spec) -> dict:
    """Merge workflow defaults + step params + system overrides."""
    merged = {
        k: v
        for k, v in workflow.defaults.items()
        if k in steps[spec.name].param_schema
    }
    merged.update(spec.params)
    merged.update(system.overrides.get(spec.name, {}))
    on_failure = merged.pop("on_failure", "auto")
    if on_failure not in ("auto", "pause"):
        raise ConfigError(
            "步骤 %s 的 on_failure 必须是 auto 或 pause: %r"
            % (spec.name, on_failure)
        )
    validated = steps[spec.name].validate_params(merged)
    validated["on_failure"] = on_failure
    return validated
