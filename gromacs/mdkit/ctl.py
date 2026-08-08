"""mdkit ctl: unified status / queue / execution / intervention control."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from typing import List, Optional

from mdkit.batch import MDKIT_SCRIPT, validate_slots
from mdkit.config import load_systems, load_workflow
from mdkit.exceptions import ConfigError
from mdkit.monitor import RunState
from mdkit.progress import load_run_workflow, step_progress
from mdkit.queue import Queue, new_item, run_locked
from mdkit.runner import (
    cmd_clean,
    cmd_retry,
    cmd_rollback,
    cmd_skip,
)


def emit(data, as_json: bool = False):
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(data)


def _load_queue(args) -> Queue:
    q = Queue(args.queue)
    if not q.exists():
        raise ConfigError("队列不存在: %s（先用 mdkit ctl init 创建）" % args.queue)
    return q


def _system_names(systems_cfg, names: Optional[List[str]]) -> List[str]:
    if not names:
        return [s.name for s in systems_cfg.systems]
    for n in names:
        if systems_cfg.system_by_name(n) is None:
            raise ConfigError("体系不存在: %s" % n)
    return list(names)


# ----------------------------------------------------------------------
# ctl init
# ----------------------------------------------------------------------
def ctl_init(args, log) -> int:
    workflow = load_workflow(args.workflow)
    systems_cfg = load_systems(args.systems)
    work_dir_base = os.path.abspath(args.work_dir_base)
    queue_path = os.path.join(work_dir_base, "queue.json")
    if os.path.isfile(queue_path):
        raise ConfigError("队列已存在: %s（如需重建请删除或用 ctl queue sync）" % queue_path)
    slots = [dict(s) for s in systems_cfg.slots]
    next_index = max((s["index"] for s in slots), default=-1) + 1
    for arg in args.slot or []:
        slots.append({"index": next_index, "args": str(arg)})
        next_index += 1
    validate_slots(slots)
    concurrency = args.concurrency or systems_cfg.concurrency
    items = {}
    for name in _system_names(systems_cfg, args.system):
        system = systems_cfg.system_by_name(name)
        items[name] = new_item(name, system.slot, os.path.join(work_dir_base, name))
    data = {
        "queue": {
            "workflow": os.path.abspath(workflow.path),
            "systems": os.path.abspath(systems_cfg.path),
            "work_dir_base": work_dir_base,
            "slots": slots,
            "concurrency": concurrency,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "stop_requested": False,
            "watch_pid": None,
        },
        "items": items,
    }
    q = Queue(queue_path)
    q.save(data)
    result = {
        "queue": queue_path,
        "systems": sorted(items),
        "slots": slots,
        "concurrency": concurrency,
    }
    emit(result, args.json)
    if not args.json and log:
        log.info("队列已创建: %s（%d 个体系，并发 %d）", queue_path, len(items), concurrency)
    return 0


# ----------------------------------------------------------------------
# ctl status
# ----------------------------------------------------------------------
def ctl_status(args, log) -> int:
    q = _load_queue(args)
    data = q.load()
    workflow = None
    try:
        workflow = load_workflow(data["queue"]["workflow"])
    except Exception:
        workflow = None
    template_loads = {}
    for s in data["queue"]["slots"]:
        template_loads[s["index"]] = 0
    for it in data["items"].values():
        if it["status"] == "running" and it.get("template") is not None:
            template_loads[it["template"]] = template_loads.get(it["template"], 0) + 1
    out = {
        "queue": data["queue"]["workflow"],
        "work_dir_base": data["queue"]["work_dir_base"],
        "concurrency": data["queue"]["concurrency"],
        "slots": data["queue"]["slots"],
        "template_loads": template_loads,
        "stop_requested": bool(data["queue"].get("stop_requested")),
        "watch_pid": data["queue"].get("watch_pid"),
        "items": {},
    }
    for name, item in data["items"].items():
        rec = dict(item)
        rs_path = os.path.join(item["run_dir"], "run_status.json")
        if os.path.isfile(rs_path):
            try:
                rs = RunState(item["run_dir"]).load()
                entry = (rs.get("systems") or {}).get(name, {})
                rec["run_status"] = entry.get("status")
                rec["step_status"] = {
                    k: v.get("status") for k, v in (entry.get("steps") or {}).items()
                }
            except Exception:
                pass
        rec["next_action"] = _next_action(rec)
        out["items"][name] = rec
    if args.json:
        emit(out, True)
        return 0
    print("队列: %s（并发 %d/%d，stop=%s）" % (
        out["queue"],
        sum(template_loads.values()),
        out["concurrency"],
        out["stop_requested"],
    ))
    print("模板占用: %s" % template_loads)
    for name, item in data["items"].items():
        rec = out["items"][name]
        print("[%s] %s%s" % (
            name,
            rec["status"],
            ("  模板 %s" % rec["template"]) if rec["status"] == "running" and rec.get("template") is not None else "",
        ))
        if rec.get("note"):
            print("    note: %s" % rec["note"])
        if rec.get("run_status") and rec["status"] == "running":
            step_status = rec.get("step_status") or {}
            running_step = [k for k, v in step_status.items() if v == "running"]
            if running_step and workflow:
                p = step_progress(workflow, item["run_dir"], name, running_step[0])
                if p:
                    print("    %s: step %s, t=%.1f ps%s" % (
                        running_step[0],
                        p.get("step"),
                        p.get("time_ps") or 0,
                        (" | %s" % p["remaining"]) if p.get("remaining") else "",
                    ))
        if rec.get("next_action"):
            print("    下一步: %s" % rec["next_action"])
    return 0


def _next_action(rec: dict) -> str:
    status = rec["status"]
    if status == "failed":
        return "修复后: ctl retry %s [<step>]（--force 从头重跑）" % rec["name"]
    if status == "repair-timeout":
        return "已超时: ctl retry --force %s 从头重跑" % rec["name"]
    if status == "held":
        return "ctl queue release %s" % rec["name"]
    if status == "done":
        return ""
    if rec.get("pending_intervention"):
        return "干预已排队，等待 watch 在当前 step 结束后应用"
    return ""


# ----------------------------------------------------------------------
# ctl queue
# ----------------------------------------------------------------------
def ctl_queue(args, log) -> int:
    q = _load_queue(args)
    action = args.queue_action
    if action == "list":
        data = q.load()
        out = [
            {"name": it["name"], "status": it["status"], "slot": it["slot"],
             "attempts": it["attempts"], "note": it["note"]}
            for it in data["items"].values()
        ]
        if args.json:
            emit({"items": out}, True)
        else:
            for it in out:
                print("[%s] %s slot=%s attempts=%s%s" % (
                    it["name"], it["status"], it["slot"], it["attempts"],
                    ("  (%s)" % it["note"]) if it.get("note") else "",
                ))
        return 0
    if action in ("add", "sync"):
        systems_path = q.load()["queue"]["systems"]

    def mutate(data: dict) -> dict:
        if action == "add":
            systems_cfg = load_systems(systems_path)
            for name in _system_names(systems_cfg, args.system):
                if name in data["items"]:
                    raise ConfigError("体系已在队列中: %s" % name)
                system = systems_cfg.system_by_name(name)
                data["items"][name] = new_item(
                    name, system.slot, os.path.join(data["queue"]["work_dir_base"], name)
                )
            return {"added": sorted(args.system or [])}
        if action == "remove":
            missing = [n for n in (args.system or []) if n not in data["items"]]
            if missing:
                raise ConfigError("队列中不存在体系: %s" % missing)
            for name in args.system or []:
                del data["items"][name]
            return {"removed": sorted(args.system or [])}
        if action == "hold":
            for name in args.system or []:
                it = data["items"].get(name)
                if it is None:
                    raise ConfigError("队列中不存在体系: %s" % name)
                if it["status"] in ("running", "done"):
                    raise ConfigError("体系 %s 状态为 %s，无法 hold" % (name, it["status"]))
                it["status"] = "held"
            return {"held": sorted(args.system or [])}
        if action == "release":
            for name in args.system or []:
                it = data["items"].get(name)
                if it is None:
                    raise ConfigError("队列中不存在体系: %s" % name)
                if it["status"] == "held":
                    it["status"] = "queued"
                    it["blocked_since"] = None
            return {"released": sorted(args.system or [])}
        if action == "sync":
            systems_cfg = load_systems(systems_path)
            before = set(data["items"])
            data["queue"]["slots"] = [dict(s) for s in systems_cfg.slots]
            data["queue"]["concurrency"] = systems_cfg.concurrency
            for system in systems_cfg.systems:
                if system.name in data["items"]:
                    data["items"][system.name]["slot"] = system.slot
                else:
                    data["items"][system.name] = new_item(
                        system.name,
                        system.slot,
                        os.path.join(data["queue"]["work_dir_base"], system.name),
                    )
            removed = sorted(before - {s.name for s in systems_cfg.systems})
            for name in removed:
                del data["items"][name]
            return {
                "added": sorted({s.name for s in systems_cfg.systems} - before),
                "removed": removed,
                "slots": data["queue"]["slots"],
                "concurrency": data["queue"]["concurrency"],
            }
        raise ConfigError("未知队列动作: %s" % action)

    result = q.locked(mutate, timeout=10.0)
    emit(result, args.json)
    if action == "sync" and result.get("removed") and not args.json and log:
        log.warning("已从队列移除（run 目录保留）: %s", result["removed"])
    return 0


# ----------------------------------------------------------------------
# ctl exec
# ----------------------------------------------------------------------
def ctl_exec(args, log) -> int:
    q = _load_queue(args)
    action = args.exec_action
    if action == "start":
        data0 = q.load()
        pid0 = data0["queue"].get("watch_pid")
        if pid0 and _pid_alive(pid0):
            raise ConfigError("watch 已在运行（pid=%s）；ctl exec status 查看" % pid0)
        argv = [sys.executable, MDKIT_SCRIPT, "watch", "--queue", q.path]
        if getattr(args, "interval", None):
            argv += ["--interval", str(args.interval)]
        if getattr(args, "repair_timeout", None):
            argv += ["--repair-timeout", str(args.repair_timeout)]
        if getattr(args, "max_wait", None):
            argv += ["--max-wait", str(args.max_wait)]
        log_path = os.path.join(data0["queue"]["work_dir_base"], "watch.log")
        fh = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            argv,
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        def record(data: dict) -> dict:
            data["queue"]["watch_pid"] = proc.pid
            data["queue"]["stop_requested"] = False
            data["queue"].pop("stop_requested_before", None)
            return {"watch_pid": proc.pid, "log": log_path, "argv": argv}

        result = q.locked(record, timeout=10.0)
        emit(result, args.json)
        if not args.json and log:
            log.info("watch 已启动（pid=%s，日志 %s）", proc.pid, log_path)
        return 0
    if action == "stop":
        def mark_stop(data: dict) -> dict:
            pid = data["queue"].get("watch_pid")
            already = bool(data["queue"].get("stop_requested_before"))
            data["queue"]["stop_requested"] = True
            data["queue"]["stop_requested_before"] = True
            if pid and _pid_alive(pid):
                os.kill(pid, signal.SIGKILL if already else signal.SIGTERM)
            return {"watch_pid": pid, "stop_requested": True, "forced": already}

        result = q.locked(mark_stop, timeout=10.0)
        emit(result, args.json)
        if not args.json and log:
            log.info("stop 已请求（watch 等在跑任务结束后退出，再次 stop 强制终止）")
        return 0
    if action == "status":
        data = q.load()
        pid = data["queue"].get("watch_pid")
        alive = bool(pid and _pid_alive(pid))
        counts = {}
        for it in data["items"].values():
            counts[it["status"]] = counts.get(it["status"], 0) + 1
        out = {
            "watch_pid": pid,
            "watch_alive": alive,
            "stop_requested": bool(data["queue"].get("stop_requested")),
            "counts": counts,
        }
        emit(out, args.json)
        return 0
    raise ConfigError("未知执行动作: %s" % action)


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ----------------------------------------------------------------------
# ctl intervention (retry / skip / rollback / clean / force)
# ----------------------------------------------------------------------
def _intervene(q: Queue, name: str, action: str, step=None, force=False,
               reason: str = "", outputs=None, from_step=None, yes=False) -> dict:
    def mutate(data: dict) -> dict:
        item = data["items"].get(name)
        if item is None:
            raise ConfigError("队列中不存在体系: %s" % name)
        run_dir = item["run_dir"]
        if run_locked(run_dir):
            # running: queue the intervention; watch applies after graceful stop
            item["pending_intervention"] = {
                "action": action,
                "step": step,
                "from_step": from_step,
                "force": force,
                "reason": reason,
                "outputs": list(outputs or []),
                "requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            return {
                "system": name,
                "action": action,
                "queued": True,
                "note": "体系正在运行，干预将在当前 step 结束后应用"
                + ("（--force 从头重跑）" if force else "（优雅中断 + checkpoint 续跑）"),
            }
        # not running: apply immediately
        if action in ("retry", "rollback"):
            if action == "retry":
                cmd_retry(run_dir, name, step)
            else:
                cmd_rollback(run_dir, name, step)
        elif action == "skip":
            cmd_skip(run_dir, name, step, reason, list(outputs or []))
        elif action == "clean":
            cmd_clean(run_dir, name, step or from_step, yes=yes)
        else:
            raise ConfigError("未知干预动作: %s" % action)
        item["pending_intervention"] = None
        item["blocked_since"] = None
        item["force"] = force
        item["status"] = "ready"
        item["note"] = "干预 %s 已应用" % action
        return {"system": name, "action": action, "queued": False, "status": "ready"}

    return q.locked(mutate, timeout=10.0)


def ctl_retry(args, log) -> int:
    q = _load_queue(args)
    result = _intervene(q, args.system, "retry", args.step, force=args.force)
    emit(result, args.json)
    return 0


def ctl_force(args, log) -> int:
    q = _load_queue(args)
    result = _intervene(q, args.system, "retry", args.step, force=True)
    emit(result, args.json)
    return 0


def ctl_skip(args, log) -> int:
    q = _load_queue(args)
    result = _intervene(
        q, args.system, "skip", args.step, reason=args.reason, outputs=args.output
    )
    emit(result, args.json)
    return 0


def ctl_rollback(args, log) -> int:
    q = _load_queue(args)
    result = _intervene(q, args.system, "rollback", args.step, force=args.force)
    emit(result, args.json)
    return 0


def ctl_clean(args, log) -> int:
    q = _load_queue(args)
    result = _intervene(
        q, args.system, "clean", from_step=args.from_step, yes=args.yes
    )
    emit(result, args.json)
    return 0
