"""mdkit command-line interface."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile

from mdkit import __version__
from mdkit import doctor as doctor_mod
from mdkit.config import ConfigError, load_systems, load_workflow
from mdkit.exceptions import MdkitError
from mdkit.monitor import RunState
from mdkit.mdp import render_mdp, resolve_template
from mdkit.registry import FileRegistry, conventions_with_dirs
from mdkit.report import build_report, render_text
from mdkit.runner import (
    Runner,
    cmd_clean as runner_cmd_clean,
    cmd_retry as runner_cmd_retry,
    cmd_rollback as runner_cmd_rollback,
    cmd_skip as runner_cmd_skip,
    effective_params,
    step_dir_for,
)
from mdkit.steps import load_steps


EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_RUN_FAILED = 2


def setup_logging(verbose: bool = False, log_path: str = "") -> logging.Logger:
    logger = logging.getLogger("mdkit")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S"
        )
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        logger.addHandler(console)
    if log_path:
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "[%(asctime)s] %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S"
            ))
            logger.addHandler(fh)
        except OSError as exc:
            logger.warning("无法创建日志文件 %s: %s", log_path, exc)
    return logger


def emit(data, as_json: bool = False):
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(data)


def _load_pair(args):
    workflow = load_workflow(args.workflow)
    systems_cfg = load_systems(args.systems)
    work_dir = systems_cfg.resolve_work_dir(getattr(args, "work_dir", None))
    return workflow, systems_cfg, work_dir


def _selected(systems_cfg, names):
    if not names:
        return systems_cfg.systems
    result = []
    for name in names:
        system = systems_cfg.system_by_name(name)
        if system is None:
            raise ConfigError("体系不存在: %s" % name)
        result.append(system)
    return result


def _register_sources(registry, system):
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


def cmd_doctor(args, log) -> int:
    report = doctor_mod.check_environment(
        tools=["obabel", "antechamber", "parmchk2", "acpype"]
    )
    if args.json:
        emit(report, True)
        return report["exit_code"]
    for check in report["checks"]:
        mark = "OK  " if check["ok"] else ("ERR " if check["required"] else "WARN")
        print("[%s] %-20s %s" % (mark, check["name"], check["detail"]))
    return report["exit_code"]


def cmd_plan(args, log) -> int:
    workflow, systems_cfg, work_dir = _load_pair(args)
    steps = load_steps(workflow.resolve_steps_dir())
    for spec in workflow.steps:
        if spec.name not in steps:
            raise ConfigError("工作流包含未知步骤: %s" % spec.name)
    produced = {}
    for spec in workflow.steps:
        for logical, _tpl, _opt in steps[spec.name].outputs:
            produced[logical] = spec.name
    result = {
        "workflow": workflow.name,
        "work_dir": work_dir,
        "layout": workflow.layout,
        "failure_policy": workflow.failure_policy,
        "systems": [],
    }
    for system in _selected(systems_cfg, args.system):
        registry = FileRegistry(
            work_dir, system, conventions=conventions_with_dirs(workflow.dirs)
        )
        _register_sources(registry, system)
        steps_out = []
        for spec in workflow.steps:
            step = steps[spec.name]
            params = effective_params(workflow, steps, system, spec)
            step_dir = step_dir_for(workflow, work_dir, system.name, spec)
            inputs = {}
            for logical in step.resolve_inputs(system):
                p = registry.get(logical)
                if p and os.path.isfile(p):
                    inputs[logical] = p
                elif logical in produced:
                    inputs[logical] = "将由步骤 %s 生成" % produced[logical]
                else:
                    inputs[logical] = "未找到"
            rec = {
                "step": spec.name,
                "dir": step_dir,
                "params": params,
                "inputs": inputs,
            }
            if "mdp" in params:
                rec["mdp"] = {
                    "spec": params["mdp"],
                    "overrides": params.get("mdp_overrides", {}),
                    "mdp_dir": workflow.resolve_mdp_dir(
                        _builtin_mdp_dir()
                    ),
                }
            steps_out.append(rec)
        result["systems"].append({"name": system.name, "steps": steps_out})
        result["systems"][-1]["review_notes"] = getattr(
            system, "review_notes", []
        )
    if args.json:
        emit(result, True)
    else:
        for sys_rec in result["systems"]:
            print("===== 体系: %s =====" % sys_rec["name"])
            for note in sys_rec.get("review_notes", []):
                print("  ⚠ %s" % note)
            for st in sys_rec["steps"]:
                print("  %-16s -> %s" % (st["step"], st["dir"]))
                if "mdp" in st:
                    print("       mdp: %s overrides=%s" % (st["mdp"]["spec"], st["mdp"]["overrides"]))
    return EXIT_OK


def cmd_run(args, log) -> int:
    workflow, systems_cfg, work_dir = _load_pair(args)
    log_path = os.path.join(work_dir, "mdkit_run.log")
    setup_logging(verbose=args.verbose, log_path=log_path)
    runner = Runner(
        workflow,
        systems_cfg,
        work_dir,
        system_filter=args.system or None,
        from_step=args.from_step,
        force=args.force,
        timeout=args.timeout,
        log=log,
    )
    code = runner.run()
    if args.json:
        emit(build_report(RunState(work_dir).load()), True)
    return code


def cmd_status(args, log) -> int:
    data = RunState(args.run_dir).load()
    if not data:
        raise ConfigError("run 状态不存在: %s" % args.run_dir)
    if args.json:
        emit(data, True)
        return EXIT_OK
    systems = data.get("systems", {})
    step_order = _workflow_step_order(data)
    hidden_pending = 0
    shown_any = False
    for name, sys_entry in systems.items():
        if args.system and name not in args.system:
            continue
        if not args.system:
            active = any(
                st.get("status") != "pending"
                for st in sys_entry.get("steps", {}).values()
            )
            if not active:
                hidden_pending += 1
                continue
        shown_any = True
        print("[%s] %s" % (name, sys_entry.get("status")))
        steps = sys_entry.get("steps", {})
        ordered = step_order + [k for k in steps if k not in step_order]
        for step_name in ordered:
            st = steps.get(step_name)
            if st is None:
                continue
            dur = " %.1fs" % st["duration_s"] if st.get("duration_s") is not None else ""
            extra = ""
            if st.get("note"):
                extra = "  (%s)" % st["note"]
            elif st.get("error"):
                extra = "  (%s)" % st["error"]
            print("  %-16s %-14s%s%s" % (step_name, st.get("status"), dur, extra))
    if hidden_pending:
        print(
            "（另有 %d 个体系在本 run 中未执行，均为 pending；"
            "使用 --system <名称> 可查看）" % hidden_pending
        )
    if not shown_any and not hidden_pending:
        print("（无体系状态）")
    return EXIT_OK


def _workflow_step_order(data: dict):
    """Return workflow step names in execution order (empty if unknown)."""
    wf_path = (data.get("run") or {}).get("workflow")
    if not wf_path or not os.path.isfile(wf_path):
        return []
    try:
        from mdkit.config import load_workflow

        return load_workflow(wf_path).step_names()
    except Exception:
        return []


def cmd_report(args, log) -> int:
    data = RunState(args.run_dir).load()
    if not data:
        raise ConfigError("run 状态不存在: %s" % args.run_dir)
    report = build_report(data)
    if args.json:
        emit(report, True)
    else:
        print(render_text(report))
    return EXIT_RUN_FAILED if report["failures"] else EXIT_OK


def cmd_mdp_show(args, log) -> int:
    workflow, systems_cfg, work_dir = _load_pair(args)
    steps = load_steps(workflow.resolve_steps_dir())
    spec = workflow.step_by_name(args.step)
    if spec is None:
        raise ConfigError("工作流中不存在步骤: %s" % args.step)
    step_cls = steps.get(spec.name)
    if step_cls is None:
        raise ConfigError("未知步骤类型: %s" % spec.name)
    system = systems_cfg.system_by_name(args.system)
    if system is None:
        raise ConfigError("体系不存在: %s" % args.system)
    params = effective_params(workflow, steps, system, spec)
    mdp_spec = params.get("mdp")
    if not mdp_spec:
        raise ConfigError("步骤 %s 没有 mdp 参数" % spec.name)
    mdp_dir = workflow.resolve_mdp_dir(_builtin_mdp_dir())
    template = resolve_template(mdp_spec, mdp_dir)
    fd, tmp = tempfile.mkstemp(suffix=".mdp", prefix="mdkit_mdp_")
    try:
        os.close(fd)
        info = render_mdp(template, params.get("mdp_overrides", {}), tmp)
        with open(tmp, "r", encoding="utf-8") as fh:
            content = fh.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if args.json:
        emit({"system": system.name, "step": spec.name, "mdp": info, "content": content}, True)
    else:
        print("# 有效 mdp（模板: %s）" % template)
        print(content)
    return EXIT_OK


def cmd_skip(args, log) -> int:
    result = runner_cmd_skip(
        args.run_dir, args.system, args.step, args.reason, args.output
    )
    emit(result, args.json)
    return EXIT_OK


def cmd_retry(args, log) -> int:
    result = runner_cmd_retry(args.run_dir, args.system, args.step)
    emit(result, args.json)
    return EXIT_OK


def cmd_rollback(args, log) -> int:
    result = runner_cmd_rollback(args.run_dir, args.system, args.step)
    emit(result, args.json)
    return EXIT_OK


def cmd_clean(args, log) -> int:
    result = runner_cmd_clean(args.run_dir, args.system, args.from_step, args.yes)
    if args.json:
        emit(result, True)
    else:
        print("清理结果（confirmed=%s）:" % result["confirmed"])
        for f in result["files"]:
            print("  - %s" % f)
    return EXIT_OK


def cmd_new_step(args, log) -> int:
    name = args.name
    if not name or not name.replace("_", "").isalnum():
        raise ConfigError("步骤名只能包含字母、数字和下划线: %s" % name)
    if args.dir:
        target_dir = os.path.abspath(args.dir)
    else:
        target_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "steps"
        )
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, name + ".py")
    if os.path.exists(path):
        raise ConfigError("步骤文件已存在: %s" % path)
    class_name = "".join(part.capitalize() for part in name.split("_")) + "Step"
    template = _STEP_TEMPLATE.replace("__NAME__", name).replace("__CLASS__", class_name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(template)
    result = {"created": path, "registered": "自动注册（steps/__init__.py 扫描）"}
    if args.json:
        emit(result, True)
    else:
        print("已生成步骤模板: %s" % path)
        print("实现 run(ctx) 后即可在 workflow.yaml 中使用步骤 %s" % name)
    return EXIT_OK


_STEP_TEMPLATE = '''"""__NAME__ step (scaffolded by mdkit new-step)."""

from __future__ import annotations

from mdkit.steps.base import Step


class __CLASS__(Step):
    name = "__NAME__"
    version = "1.0"
    description = "TODO: 描述此步骤"
    inputs = []            # 逻辑输入名，例如 ["processed_gro"]
    outputs = []           # 例如 [("my_out", "{system}_my_out.dat", False)]
    param_schema = {
        # "my_param": {"type": str, "default": "value"},
    }
    env_requirements = []

    def run(self, ctx) -> None:
        # 只在本步骤目录（ctx.cwd）内写文件；gmx 必须走 ctx.run_gmx()。
        # 示例：
        # out = ctx.register_output("my_out", "{system}_my_out.dat".format(system=ctx.system.name))
        # with open(out, "w") as fh:
        #     fh.write("hello\\n")
        raise NotImplementedError("请实现 %s.run()" % self.name)
'''


def _builtin_mdp_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "configs", "mdp")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdkit",
        description="mdkit - 模块化 GROMACS MD 工作流工具（v%s）" % __version__,
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="环境检查")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("plan", help="dry-run 预览")
    p.add_argument("-w", "--workflow", required=True)
    p.add_argument("-s", "--systems", required=True)
    p.add_argument("--system", action="append")
    p.add_argument("--work-dir")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("run", help="执行工作流")
    p.add_argument("-w", "--workflow", required=True)
    p.add_argument("-s", "--systems", required=True)
    p.add_argument("--system", action="append")
    p.add_argument("--from", dest="from_step", help="从指定步骤开始")
    p.add_argument("--force", action="store_true", help="强制重跑已完成的步骤")
    p.add_argument("--work-dir")
    p.add_argument("--timeout", type=float, default=None, help="单条命令超时秒数")
    p.add_argument("--json", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="查询运行状态")
    p.add_argument("run_dir")
    p.add_argument("--system", action="append")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("report", help="汇总报告与错误清单")
    p.add_argument("run_dir")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("mdp-show", help="查看渲染后的有效 mdp")
    p.add_argument("-w", "--workflow", required=True)
    p.add_argument("-s", "--systems", required=True)
    p.add_argument("--system", required=True)
    p.add_argument("step")
    p.add_argument("--work-dir")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_mdp_show)

    p = sub.add_parser("skip", help="人工/Codex 放行步骤")
    p.add_argument("run_dir")
    p.add_argument("system")
    p.add_argument("step")
    p.add_argument("--reason", default="")
    p.add_argument("--output", action="append", default=[], help="logical=path")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_skip)

    p = sub.add_parser("retry", help="重置步骤为待执行")
    p.add_argument("run_dir")
    p.add_argument("system")
    p.add_argument("step", nargs="?", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_retry)

    p = sub.add_parser("rollback", help="回退步骤并失效下游（不删除文件）")
    p.add_argument("run_dir")
    p.add_argument("system")
    p.add_argument("step", nargs="?", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("clean", help="删除失效输出（需确认）")
    p.add_argument("run_dir")
    p.add_argument("system")
    p.add_argument("--from", dest="from_step")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("new-step", help="生成步骤模块脚手架")
    p.add_argument("name")
    p.add_argument("--dir", help="外部步骤目录（默认内置 steps/）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_new_step)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    log = setup_logging(verbose=getattr(args, "verbose", False))
    try:
        return int(args.func(args, log) or EXIT_OK)
    except ConfigError as exc:
        log.error("配置错误: %s", exc)
        return EXIT_CONFIG
    except MdkitError as exc:
        log.error("%s", exc)
        return EXIT_CONFIG
    except KeyboardInterrupt:
        log.warning("用户中断")
        return 130
