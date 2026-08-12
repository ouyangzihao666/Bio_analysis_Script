"""Long-running queue executor: mdkit watch.

``mdkit watch --queue <queue.json>`` consumes a ctl queue and launches
``mdkit run`` for each system when the item is launchable, the global
concurrency limit is not reached and the run directory is unlocked.
Interventions requested via ``mdkit ctl retry/skip/...`` while a system is
running are applied after a graceful SIGTERM (checkpoint preserved and the
simulation step resumed with ``-cpi``); ``--force`` reruns the step from
scratch instead.

``mdkit watch <run_dir>`` supervises a single run directory directly
(sequential, no queue manifest).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from typing import List, Optional

from mdkit.batch import MDKIT_SCRIPT, merge_cli_options, write_temp_systems
from mdkit.config import load_systems, load_workflow
from mdkit.exceptions import ConfigError
from mdkit.monitor import RunState
from mdkit.queue import Queue, new_item, run_locked
from mdkit.runner import (
    cmd_clean,
    cmd_retry,
    cmd_rollback,
    cmd_skip,
    effective_params,
    step_dir_for,
)
from mdkit.steps import load_steps


SIM_STEPS = ("em", "nvt", "npt", "md")


class Watch:
    """Poll-driven scheduler for a queue manifest or a single run dir."""

    def __init__(
        self,
        queue_path: Optional[str] = None,
        run_dir: Optional[str] = None,
        interval_min: float = 3.0,
        repair_timeout_min: float = 30.0,
        max_wait_min: Optional[float] = None,
        log=None,
        json_events: bool = False,
    ):
        if bool(queue_path) == bool(run_dir):
            raise ConfigError("watch 需要 --queue <queue.json> 或 <run_dir> 之一")
        self.queue_path = queue_path
        self.run_dir = run_dir
        self.interval = max(1.0, interval_min * 60.0)
        self.repair_timeout = repair_timeout_min * 60.0
        self.max_wait = max_wait_min * 60.0 if max_wait_min else None
        self.log = log
        self.json_events = json_events
        self.spawned = {}
        self.zombie = {}
        self._interrupted = False
        self._stop_requested = False
        self._sig_count = 0
        self._empty_logged = False

    # -- setup ---------------------------------------------------------
    def _setup(self) -> None:
        if self.queue_path:
            self.queue = Queue(self.queue_path)
            data = self.queue.load()
            q = data["queue"]
            self.workflow = load_workflow(q["workflow"])
            self.systems_cfg = load_systems(q["systems"])
            self.work_dir_base = q["work_dir_base"]
            self.slots = q["slots"]
            self.concurrency = q["concurrency"]
            self.single_mode = False
        else:
            self.queue = None
            data = RunState(self.run_dir).load()
            if not data:
                raise ConfigError("run 状态不存在: %s" % self.run_dir)
            run = data.get("run", {})
            if not run.get("workflow") or not run.get("systems"):
                raise ConfigError(
                    "run 目录缺少 workflow/systems 记录，无法监督: %s" % self.run_dir
                )
            self.workflow = load_workflow(run["workflow"])
            self.systems_cfg = load_systems(run["systems"])
            self.work_dir_base = os.path.abspath(self.run_dir)
            self.slots = [{"index": 0, "args": ""}]
            self.concurrency = 1
            self.single_mode = True
        self.steps = load_steps(self.workflow.resolve_steps_dir())
        self.md_spec = self.workflow.step_by_name("md")
        self._install_signal_handlers()

    def _install_signal_handlers(self) -> None:
        def handler(signum, frame):
            self._sig_count += 1
            if self._sig_count >= 2:
                self._log("二次信号，强制终止子进程")
                self._kill_children(signal.SIGKILL)
            else:
                self._interrupted = True
                self._log("收到信号 %s，停止接收新任务（再次发送则强制终止）", signum)

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    # -- logging -------------------------------------------------------
    def _log(self, fmt: str, *args) -> None:
        if self.log:
            self.log.info(fmt, *args)

    def _event(self, obj: dict) -> None:
        if self.json_events:
            obj["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
            print(json.dumps(obj, ensure_ascii=False, sort_keys=True))

    # -- items ---------------------------------------------------------
    def _items(self, data: dict) -> List[dict]:
        if not self.single_mode:
            return list(data["items"].values())
        # single-run mode: derive items from run_status.json
        rs = RunState(self.run_dir).load()
        items = []
        for name, entry in (rs.get("systems") or {}).items():
            status = entry.get("status")
            item = new_item(name, None, self.run_dir)
            if status == "done":
                item["status"] = "done"
            elif status in ("failed", "paused", "interrupted"):
                item["status"] = "failed"
                item["note"] = "run 状态: %s" % status
            else:
                item["status"] = "queued"
            items.append(item)
        return items

    def _item(self, items: List[dict], name: str) -> Optional[dict]:
        for it in items:
            if it["name"] == name:
                return it
        return None

    def _system(self, name: str):
        system = self.systems_cfg.system_by_name(name)
        if system is None:
            raise ConfigError("体系不存在: %s" % name)
        return system

    # -- child processes ----------------------------------------------
    def _signal_proc(self, info: dict, sig: int) -> None:
        try:
            os.killpg(os.getpgid(info["proc"].pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                info["proc"].terminate()
            except Exception:
                pass

    def _kill_children(self, sig: int) -> None:
        for info in list(self.spawned.values()):
            if info["proc"].poll() is None:
                self._signal_proc(info, sig)

    # -- scheduling helpers -------------------------------------------
    def _template_args(self, index: int) -> str:
        for s in self.slots:
            if s["index"] == index:
                return s["args"]
        raise ConfigError("槽位不存在: %s" % index)

    def _least_loaded(self, items: List[dict]) -> int:
        loads = {s["index"]: 0 for s in self.slots}
        for name, info in self.spawned.items():
            if info.get("template") in loads:
                loads[info["template"]] += 1
        return min(loads, key=lambda k: loads[k])

    def _merged_extra(self, item: dict, slot_args: str) -> str:
        system = self._system(item["name"])
        eff = effective_params(self.workflow, self.steps, system, self.md_spec)
        base = eff.get("extra_args") or ""
        merged = merge_cli_options(shlex.split(base), shlex.split(slot_args))
        return shlex.join(merged)

    def _launch(self, data: dict, items: List[dict]) -> None:
        if self._interrupted or self._stop_requested:
            return
        if self.single_mode:
            if self.spawned:
                return
            if any(it["status"] in ("queued", "ready") for it in items):
                self._launch_single()
            return
        running = len(self.spawned)
        for item in items:
            if running >= self.concurrency:
                return
            if item["status"] not in ("queued", "ready"):
                continue
            if item["name"] in self.spawned:
                continue
            if run_locked(item["run_dir"]):
                continue
            tmpl = item["slot"] if item["slot"] is not None else self._least_loaded(items)
            slot_args = self._template_args(tmpl)
            run_dir = item["run_dir"]
            tmp_systems = os.path.join(
                os.path.dirname(run_dir), ".batch", "%s.yaml" % item["name"]
            )
            merged = self._merged_extra(item, slot_args)
            extra = item.get("resume") or {}
            write_temp_systems(
                self.workflow,
                self._system(item["name"]),
                merged,
                tmp_systems,
                extra_overrides=extra,
            )
            argv = [
                sys.executable,
                MDKIT_SCRIPT,
                "run",
                "-w",
                self.workflow.path,
                "-s",
                tmp_systems,
                "--system",
                item["name"],
                "--work-dir",
                run_dir,
                "--json",
            ]
            console_path = os.path.join(
                os.path.dirname(run_dir), "console_%s.log" % item["name"]
            )
            console = open(console_path, "a", encoding="utf-8")
            proc = subprocess.Popen(
                argv,
                stdout=console,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            item["status"] = "running"
            item["template"] = tmpl
            item["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            item["attempts"] = int(item.get("attempts", 0)) + 1
            item["blocked_since"] = None
            self.spawned[item["name"]] = {
                "proc": proc,
                "template": tmpl,
                "run_dir": run_dir,
                "console": console,
                "start": time.time(),
                "signaled": False,
            }
            running += 1
            self._log(
                "[%s] 启动（模板 %s，并发 %d/%d）",
                item["name"],
                tmpl,
                running,
                self.concurrency,
            )
            self._event({"type": "launch", "system": item["name"], "template": tmpl})

    def _launch_single(self) -> None:
        run = RunState(self.run_dir).load().get("run", {})
        argv = [
            sys.executable,
            MDKIT_SCRIPT,
            "run",
            "-w",
            run["workflow"],
            "-s",
            run["systems"],
            "--work-dir",
            self.run_dir,
            "--json",
        ]
        console_path = os.path.join(
            os.path.dirname(self.run_dir), "console_%s.log" % os.path.basename(self.run_dir)
        )
        console = open(console_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            argv,
            stdout=console,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.spawned["__run__"] = {
            "proc": proc,
            "template": 0,
            "run_dir": self.run_dir,
            "console": console,
            "start": time.time(),
            "signaled": False,
        }
        self._log("启动单 run 监督: %s", self.run_dir)

    # -- intervention --------------------------------------------------
    def _apply_intervention(self, item: dict, pending: dict) -> None:
        action = pending.get("action", "retry")
        step = pending.get("step")
        force = bool(pending.get("force"))
        if action in ("retry", "rollback"):
            if not force:
                # 先保留 checkpoint（retry 会把 interrupted 重置为 pending）
                resume = self._preserve_checkpoints(item)
                if resume:
                    item["resume"] = resume
                    self._log(
                        "[%s] 已保留 checkpoint，续跑: %s",
                        item["name"],
                        ", ".join(resume),
                    )
            if action == "retry":
                cmd_retry(
                    item["run_dir"],
                    item["name"],
                    step,
                    select=pending.get("select"),
                )
            else:
                cmd_rollback(item["run_dir"], item["name"], step)
        elif action == "skip":
            cmd_skip(
                item["run_dir"],
                item["name"],
                step,
                pending.get("reason", ""),
                pending.get("outputs") or [],
            )
        elif action == "clean":
            cmd_clean(
                item["run_dir"], item["name"], step or pending.get("from_step"), yes=True
            )
        else:
            raise ConfigError("未知干预动作: %s" % action)
        item["pending_intervention"] = None
        item["blocked_since"] = None
        item["force"] = force
        item["status"] = "ready"
        item["note"] = "干预 %s 已应用" % action
        self._event(
            {
                "type": "intervention_applied",
                "system": item["name"],
                "action": action,
                "force": force,
            }
        )

    def _preserve_checkpoints(self, item: dict) -> dict:
        """Copy interrupted sim-step checkpoints from .stage into .resume."""
        resume = {}
        try:
            rs = RunState(item["run_dir"]).load()
        except Exception:
            return resume
        entry = (rs.get("systems") or {}).get(item["name"], {})
        for step_name in SIM_STEPS:
            spec = self.workflow.step_by_name(step_name)
            if spec is None:
                continue
            st = entry.get("steps", {}).get(step_name, {})
            if st.get("status") not in ("interrupted", "failed"):
                continue
            step_dir = step_dir_for(self.workflow, item["run_dir"], item["name"], spec)
            deffnm = "%s_%s" % (item["name"], step_name)
            cpt = os.path.join(step_dir, self.workflow.stage_name, deffnm + ".cpt")
            if not os.path.isfile(cpt):
                continue
            res_dir = os.path.join(step_dir, ".resume")
            os.makedirs(res_dir, exist_ok=True)
            dst = os.path.join(res_dir, deffnm + ".cpt")
            shutil.copy2(cpt, dst)
            resume[step_name] = {"continue_cpt": dst}
        return resume

    def _handle_exit(self, item: dict, rc: int) -> None:
        item["last_exit"] = rc
        item["template"] = None
        item["started_at"] = None
        item["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        pending = item.get("pending_intervention")
        if pending:
            self._log(
                "[%s] run 进程退出（rc=%s），应用干预 %s",
                item["name"],
                rc,
                pending.get("action"),
            )
            self._apply_intervention(item, pending)
            return
        self._sync_item_from_run(item)
        self._event(
            {
                "type": "run_finished",
                "system": item["name"],
                "exit": rc,
                "status": item["status"],
            }
        )

    def _sync_item_from_run(self, item: dict) -> None:
        try:
            rs = RunState(item["run_dir"]).load()
            entry = (rs.get("systems") or {}).get(item["name"], {})
            status = entry.get("status")
        except Exception:
            status = None
        if status == "done":
            item["status"] = "done"
            item["note"] = None
            item["blocked_since"] = None
            item["force"] = False
            item.pop("resume", None)
            self._log("[%s] 体系完成", item["name"])
        elif status in ("failed", "paused", "interrupted"):
            item["status"] = "failed"
            item["note"] = "run 状态: %s（修复后 ctl retry/skip）" % status
            self._log("[%s] 体系阻塞（run 状态 %s），等待干预", item["name"], status)
        else:
            item["status"] = "queued"

    # -- main loop -----------------------------------------------------
    def run(self) -> int:
        self._setup()
        start = time.time()
        self._log(
            "watch 启动（interval=%.0fs repair_timeout=%.0fs%s）",
            self.interval,
            self.repair_timeout,
            (" max_wait=%.0fs" % self.max_wait) if self.max_wait else "",
        )
        while True:
            if self.queue is None:
                self._poll_items(self._items(None), None)
            else:
                self.queue.locked(
                    lambda d: self._poll_items(self._items(d), d), timeout=5.0
                )
            code = self._check_exit(start)
            if code is not None:
                self._shutdown()
                self._log("watch 结束，退出码 %d", code)
                return code
            empty = False
            if self.queue is not None:
                try:
                    empty = not self.queue.load().get("items")
                except Exception:
                    empty = False
            if empty:
                if not self._empty_logged:
                    self._log(
                        "队列为空，watch 持续等待；可 ctl queue add 加入体系"
                        "（Ctrl+C 或 ctl exec stop 退出）"
                    )
                    self._empty_logged = True
            elif self._empty_logged:
                self._empty_logged = False
            # 停止流程中缩短轮询粒度：强制终止的子进程退出后能尽快收尾退出
            if self._interrupted or self._stop_requested:
                time.sleep(min(self.interval, 2.0))
            else:
                time.sleep(self.interval)

    def _poll_items(self, items: List[dict], data) -> None:
        # 1. intervention on running systems -> graceful SIGTERM
        for item in items:
            if item.get("pending_intervention") and item["name"] in self.spawned:
                info = self.spawned[item["name"]]
                if not info.get("signaled"):
                    self._signal_proc(info, signal.SIGTERM)
                    info["signaled"] = True
                    self._log(
                        "[%s] 干预 %s 请求：发送 SIGTERM（优雅中断 + checkpoint）",
                        item["name"],
                        item["pending_intervention"].get("action"),
                    )
        # 2. collect finished processes
        for name, info in list(self.spawned.items()):
            rc = info["proc"].poll()
            if rc is not None:
                del self.spawned[name]
                try:
                    info["console"].close()
                except Exception:
                    pass
                if self.single_mode:
                    continue
                item = self._item(items, name)
                if item is not None:
                    self._handle_exit(item, rc)
        # 3. zombie recovery
        for item in items:
            if item["status"] == "running" and item["name"] not in self.spawned:
                if run_locked(item["run_dir"]):
                    self.zombie[item["name"]] = 0
                else:
                    self.zombie[item["name"]] = self.zombie.get(item["name"], 0) + 1
                    if self.zombie[item["name"]] >= 2:
                        self._log(
                            "[%s] 疑似崩溃（running 但无进程且锁空闲），重新排队",
                            item["name"],
                        )
                        item["status"] = "queued"
                        self.zombie[item["name"]] = 0
            else:
                self.zombie.pop(item["name"], None)
        # 4. repair timeout
        now = time.time()
        for item in items:
            if item["status"] == "failed" and not item.get("pending_intervention"):
                if item.get("blocked_since") is None:
                    item["blocked_since"] = now
                elif self.repair_timeout and (now - item["blocked_since"]) > self.repair_timeout:
                    item["status"] = "repair-timeout"
                    item["note"] = "超过修复时限 %.0f min 未干预" % (self.repair_timeout / 60)
                    self._log(
                        "[%s] 修复超时（%.0f min），标记 repair-timeout",
                        item["name"],
                        self.repair_timeout / 60,
                    )
                    self._event(
                        {"type": "repair-timeout", "system": item["name"]}
                    )
            elif item["status"] in ("queued", "ready", "running"):
                item["blocked_since"] = None
        # 5. stop_requested / interrupted bookkeeping
        if data is not None:
            self._stop_requested = bool(data["queue"].get("stop_requested"))
        # 6. launch new work
        self._launch(data, items)
        return data

    def _check_exit(self, start: float) -> Optional[int]:
        items = self._items(None) if self.single_mode else list(self.queue.load()["items"].values())
        statuses = [it["status"] for it in items]
        if (self._stop_requested or self._interrupted) and not self.spawned:
            return 130
        if statuses and all(s == "done" for s in statuses):
            return 0
        if statuses and all(s in ("done", "repair-timeout") for s in statuses):
            return 2 if any(s == "repair-timeout" for s in statuses) else 0
        if self.max_wait and (time.time() - start) > self.max_wait:
            return 2
        return None

    def _shutdown(self) -> None:
        for info in list(self.spawned.values()):
            try:
                info["console"].close()
            except Exception:
                pass
        self.spawned.clear()


def run_watch(
    queue_path=None,
    run_dir=None,
    interval_min=3.0,
    repair_timeout_min=30.0,
    max_wait_min=None,
    log=None,
    json_events=False,
) -> int:
    watch = Watch(
        queue_path=queue_path,
        run_dir=run_dir,
        interval_min=interval_min,
        repair_timeout_min=repair_timeout_min,
        max_wait_min=max_wait_min,
        log=log,
        json_events=json_events,
    )
    return watch.run()
