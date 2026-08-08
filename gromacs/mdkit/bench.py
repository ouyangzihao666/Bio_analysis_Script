"""Benchmark suite runner: sequential tests, slot concurrency, GPU/CPU sampling."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time

from mdkit.batch import SlotScheduler, parse_slots, slot_gpu
from mdkit.config import load_yaml
from mdkit.progress import step_progress


DEFAULT_WINDOW = (3000.0, 7000.0)  # ps (3-7 ns)
SAMPLE_COUNT = 3
SAMPLE_INTERVAL = 10.0


def load_suite(path: str) -> dict:
    data = load_yaml(path)
    tests = data.get("tests")
    if not isinstance(tests, list) or not tests:
        raise ValueError("bench 套件缺少 tests 列表: %s" % path)
    out = []
    for t in tests:
        name = t.get("name")
        slots = t.get("slots")
        if not name or not isinstance(slots, list) or not slots:
            raise ValueError("bench 测试必须包含 name 与非空 slots: %r" % t)
        out.append(
            {
                "name": str(name),
                "slots": [
                    {"name": "slot_%d" % i, "args": str(s)}
                    for i, s in enumerate(slots)
                ],
                "systems": t.get("systems") or [],
            }
        )
    return {"tests": out}


def gpu_available() -> bool:
    try:
        r = subprocess.run(
            ["nvidia-smi", "-L"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def sample_gpu() -> dict:
    """Return {gpu_id: {"util": %, "mem_mib": n}}."""
    out = {}
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        for line in r.stdout.decode(errors="replace").splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                out[parts[0]] = {
                    "util": _to_float(parts[1]),
                    "mem_mib": _to_float(parts[2]),
                }
    except Exception:
        pass
    return out


def sample_cpu(mdrun_pid) -> dict:
    cpu = None
    if mdrun_pid:
        try:
            r = subprocess.run(
                ["ps", "-o", "%cpu=", "-p", str(mdrun_pid)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            val = r.stdout.decode(errors="replace").strip()
            if val:
                cpu = _to_float(val)
        except Exception:
            cpu = None
    loadavg = None
    try:
        with open("/proc/loadavg") as fh:
            loadavg = fh.read().strip().split()[0]
    except Exception:
        pass
    return {"cpu_pct": cpu, "loadavg": loadavg}


def find_mdrun_pid(system_name: str):
    try:
        r = subprocess.run(
            ["pgrep", "-f", "mdrun.*-deffnm %s_md" % system_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        pids = r.stdout.decode(errors="replace").split()
        return int(pids[0]) if pids else None
    except Exception:
        return None


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Sampler:
    """Samples GPU/CPU once per system when its mdrun crosses the window."""

    def __init__(self, workflow, gpu_ok: bool, window=DEFAULT_WINDOW, log=None):
        self.workflow = workflow
        self.gpu_ok = gpu_ok
        self.window = window
        self.log = log
        self.samples = {}
        self._triggered = set()

    def tick(self, running) -> None:
        for name, info in running.items():
            if name in self._triggered:
                continue
            prog = step_progress(self.workflow, info["rundir"], name, "md")
            if not prog:
                continue
            t_ps = prog["time_ps"]
            if self.window[0] <= t_ps < self.window[1]:
                self._triggered.add(name)
                self._burst(name, info)

    def _burst(self, name, info) -> None:
        rec = {"system": name, "slot": info["slot"], "samples": []}
        for i in range(SAMPLE_COUNT):
            if i:
                time.sleep(SAMPLE_INTERVAL)
            prog = step_progress(self.workflow, info["rundir"], name, "md")
            gpu = sample_gpu() if self.gpu_ok else {}
            pid = find_mdrun_pid(name)
            cpu = sample_cpu(pid)
            rec["samples"].append(
                {
                    "sample": i + 1,
                    "step": prog["step"] if prog else None,
                    "time_ps": prog["time_ps"] if prog else None,
                    "gpu": gpu,
                    "cpu_pct": cpu["cpu_pct"],
                    "loadavg": cpu["loadavg"],
                    "mdrun_pid": pid,
                }
            )
            if self.log:
                self.log.info(
                    "[%s] 采样 %d/%d t=%.0f ps gpu=%s cpu=%s",
                    name,
                    i + 1,
                    SAMPLE_COUNT,
                    rec["samples"][-1]["time_ps"] or 0,
                    gpu,
                    cpu["cpu_pct"],
                )
        self.samples[name] = rec


def _summarize(test_name, slots, systems, results, wall_s, samples) -> dict:
    per_gpu = {}
    per_system = {}
    for name, rec in samples.items():
        cpu_vals = [s["cpu_pct"] for s in rec["samples"] if s["cpu_pct"] is not None]
        per_system[name] = {
            "avg_cpu_pct": round(sum(cpu_vals) / len(cpu_vals), 1) if cpu_vals else None,
            "samples": len(rec["samples"]),
        }
        gpu_id = slot_gpu(slots[rec["slot"]])
        if gpu_id is not None:
            utils = []
            for s in rec["samples"]:
                g = s["gpu"].get(gpu_id)
                if g and g["util"] is not None:
                    utils.append(g["util"])
            per_gpu.setdefault(gpu_id, []).extend(utils)
    gpu_summary = {
        gid: {
            "avg_util_pct": round(sum(v) / len(v), 1) if v else None,
            "samples": len(v),
        }
        for gid, v in per_gpu.items()
    }
    return {
        "test": test_name,
        "slots": [s["args"] for s in slots],
        "systems": list(systems),
        "results": results,
        "wall_s": wall_s,
        "per_system": per_system,
        "per_gpu": gpu_summary,
    }


def run_bench(workflow_path, systems_path, work_dir_base, suite_path, log=None, system_filter=None):
    suite = load_suite(suite_path)
    gpu_ok = gpu_available()
    if log:
        log.info("nvidia-smi 可用: %s（%s）", gpu_ok, "将采样 GPU" if gpu_ok else "仅采样 CPU")
    scheduler = SlotScheduler(workflow_path, systems_path, work_dir_base, log=log)
    summaries = []
    failed_any = False
    for test in suite["tests"]:
        systems = test["systems"] or [s.name for s in scheduler.systems_cfg.systems]
        if system_filter:
            systems = [s for s in systems if s in system_filter]
        if not systems:
            if log:
                log.warning("测试 %s 无体系可运行，跳过", test["name"])
            continue
        if log:
            log.info(
                "===== 测试 %s：并发 %d，体系 %s =====",
                test["name"],
                len(test["slots"]),
                systems,
            )
        sampler = Sampler(scheduler.workflow, gpu_ok, log=log)
        results, wall_s, test_dir = scheduler.run_test(
            test["name"], systems, test["slots"], tick=sampler.tick
        )
        summary = _summarize(
            test["name"], test["slots"], systems, results, wall_s, sampler.samples
        )
        summaries.append(summary)
        with open(os.path.join(test_dir, "benchmark.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2, sort_keys=True)
        failed = any(r["exit"] != 0 for r in results.values())
        failed_any = failed_any or failed
        if log:
            log.info("测试 %s 完成，墙钟 %.1fs，失败=%s", test["name"], wall_s, failed)
    if log and summaries:
        _print_summary(log, summaries)
    return 2 if failed_any else 0


def _print_summary(log, summaries) -> None:
    log.info("===== 基准对比汇总 =====")
    for s in summaries:
        gpu = ", ".join(
            "GPU%s=%.1f%%" % (gid, v["avg_util_pct"])
            for gid, v in s["per_gpu"].items()
            if v["avg_util_pct"] is not None
        )
        cpu = ", ".join(
            "%s=%.1f%%" % (name, v["avg_cpu_pct"])
            for name, v in s["per_system"].items()
            if v["avg_cpu_pct"] is not None
        )
        log.info(
            "[%s] wall=%.1fs | GPU: %s | CPU: %s",
            s["test"],
            s["wall_s"],
            gpu or "无",
            cpu or "无",
        )
