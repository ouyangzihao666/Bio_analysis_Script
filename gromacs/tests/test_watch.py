"""ctl / watch unified controller tests (unit + fake-gmx integration)."""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
import unittest

from mdkit.config import ConfigError, load_systems
from mdkit.exceptions import RunError
from mdkit.queue import Queue, QueueLock
from mdkit.watch import Watch

from tests.helpers import TempWorkspace, make_fake_gmx, with_fake_path


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MDKIT = os.path.join(REPO, "mdkit", "mdkit")
MDP = os.path.join(REPO, "configs", "mdp")


def _workflow(ws, extra_steps=""):
    return ws.write(
        "workflow.yaml",
        "name: w\n"
        "failure_policy: continue\n"
        "layout: per_step\n"
        "mdp_dir: %s\n"
        "steps:\n"
        "  - step: env_check\n"
        "  - step: protein_prep\n"
        "  - step: box\n"
        "  - step: solvate\n"
        "  - step: ions\n"
        "  - step: em\n"
        "  - step: nvt\n"
        "  - step: npt\n"
        "  - step: md\n"
        "  - step: index\n"
        "  - step: traj_correct\n"
        "%s" % (MDP, extra_steps),
    )


def _run_cli(env, *argv, timeout=60):
    return subprocess.run(
        [sys.executable, MDKIT, *argv],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _start_watch(env, log_path, *args):
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, MDKIT, "watch", *args],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return proc, log


def _wait_queue(qp, pred, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = _load_json(qp)
        if pred(data):
            return data
        time.sleep(0.3)
    raise AssertionError("等待队列状态超时: %s" % qp)


# ----------------------------------------------------------------------
# unit tests
# ----------------------------------------------------------------------
class CtlUnitTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    def test_systems_slots_parsing(self):
        path = self.ws.write(
            "systems.yaml",
            "work_dir: ./result\n"
            "slots:\n"
            '  0: "-ntmpi 1 -ntomp 8 -gpu_id 0"\n'
            '  1: "-ntmpi 1 -ntomp 8 -gpu_id 1"\n'
            "concurrency: 4\n"
            "systems:\n"
            "  - name: a\n"
            "    protein: {file: inputs/protein_A.pdb}\n"
            "    slot: 1\n"
            "  - name: b\n"
            "    protein: {file: inputs/protein_B.pdb}\n"
            "    ligands: []\n",
        )
        self.ws.add_protein("protein_A.pdb")
        self.ws.add_protein("protein_B.pdb")
        cfg = load_systems(path)
        self.assertEqual(cfg.slots, [
            {"index": 0, "args": "-ntmpi 1 -ntomp 8 -gpu_id 0"},
            {"index": 1, "args": "-ntmpi 1 -ntomp 8 -gpu_id 1"},
        ])
        self.assertEqual(cfg.concurrency, 4)
        self.assertEqual(cfg.system_by_name("a").slot, 1)
        self.assertIsNone(cfg.system_by_name("b").slot)

    def test_systems_slots_default(self):
        path = self.ws.write(
            "systems.yaml",
            "systems:\n"
            "  - name: a\n"
            "    protein: {file: inputs/protein_A.pdb}\n",
        )
        self.ws.add_protein("protein_A.pdb")
        cfg = load_systems(path)
        self.assertEqual(cfg.slots, [{"index": 0, "args": ""}])
        self.assertEqual(cfg.concurrency, 1)

    def test_systems_slots_validation(self):
        self.ws.add_protein("protein_A.pdb")
        path = self.ws.write(
            "systems.yaml",
            "slots:\n"
            "  -1: \"x\"\n"
            "systems:\n"
            "  - name: a\n"
            "    protein: {file: inputs/protein_A.pdb}\n",
        )
        with self.assertRaises(ConfigError):
            load_systems(path)
        path2 = self.ws.write(
            "systems2.yaml",
            "slots:\n"
            '  0: "-ntmpi 1"\n'
            "systems:\n"
            "  - name: a\n"
            "    protein: {file: inputs/protein_A.pdb}\n"
            "    slot: 3\n",
        )
        with self.assertRaises(ConfigError):
            load_systems(path2)

    def test_queue_manifest_lock(self):
        qp = os.path.join(self.ws.root, "out", "queue.json")
        q = Queue(qp)
        q.save({"queue": {}, "items": {}})
        self.assertTrue(q.exists())
        self.assertEqual(q.load(), {"queue": {}, "items": {}})
        lock = QueueLock(qp)
        lock.acquire()
        try:
            with self.assertRaises(RunError):
                QueueLock(qp).acquire()
        finally:
            lock.release()

    def test_watch_least_loaded(self):
        w = Watch.__new__(Watch)
        w.slots = [{"index": 0, "args": ""}, {"index": 1, "args": ""}]
        w.spawned = {"a": {"template": 1}, "b": {"template": 1}}
        self.assertEqual(w._least_loaded([]), 0)
        w.spawned = {"a": {"template": 0}, "b": {"template": 1}}
        self.assertEqual(w._least_loaded([]), 0)


# ----------------------------------------------------------------------
# integration tests (fake gmx)
# ----------------------------------------------------------------------
class WatchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()
        self.ws.add_protein("protein_A.pdb")
        self.ws.add_protein("protein_B.pdb")
        self.fake = make_fake_gmx()
        self.outbase = os.path.join(self.ws.root, "out")
        self.qp = os.path.join(self.outbase, "queue.json")
        self.flag = os.path.join(self.ws.root, "fail.flag")

    def tearDown(self):
        self.ws.cleanup()

    def _env(self, **extra):
        env = with_fake_path(self.fake)
        env.update(extra)
        return env

    def _init_queue(self, systems_block, workflow=None, concurrency=None):
        wf = workflow or _workflow(self.ws)
        argv = ["ctl", "init", "-w", wf, "-s", systems_block, "--work-dir-base", self.outbase]
        if concurrency:
            argv += ["--concurrency", str(concurrency)]
        r = _run_cli(self._env(), *argv)
        self.assertEqual(r.returncode, 0, r.stderr)
        return wf

    def test_full_flow_fail_then_retry(self):
        systems = self.ws.write(
            "systems.yaml",
            "work_dir: ./result\n"
            "slots:\n"
            '  0: "-ntmpi 1 -ntomp 8 -gpu_id 0"\n'
            '  1: "-ntmpi 1 -ntomp 8 -gpu_id 1"\n'
            "systems:\n"
            "  - name: protA\n"
            "    protein: {file: inputs/protein_A.pdb}\n"
            "  - name: protB\n"
            "    protein: {file: inputs/protein_B.pdb}\n"
            "    slot: 1\n",
        )
        self._init_queue(systems, concurrency=2)
        env = self._env(FAKE_GMX_FAIL_FILE=self.flag)
        with open(self.flag, "w") as fh:
            fh.write("fail\n")
        wlog = os.path.join(self.ws.root, "watch.log")
        proc, fh = _start_watch(
            env, wlog, "--queue", self.qp, "--interval", "0.01", "--max-wait", "2"
        )
        try:
            _wait_queue(
                self.qp,
                lambda d: all(v["status"] == "failed" for v in d["items"].values()),
            )
            os.remove(self.flag)
            for name in ("protA", "protB"):
                r = _run_cli(env, "ctl", "retry", "-q", self.qp, name)
                self.assertEqual(r.returncode, 0, r.stderr)
            rc = proc.wait(timeout=120)
        finally:
            fh.close()
        self.assertEqual(rc, 0)
        data = _load_json(self.qp)
        for name in ("protA", "protB"):
            self.assertEqual(data["items"][name]["status"], "done")
            self.assertEqual(data["items"][name]["attempts"], 2)

    def test_repair_timeout_exit2(self):
        systems = self.ws.write(
            "systems.yaml",
            "systems:\n"
            "  - name: protA\n"
            "    protein: {file: inputs/protein_A.pdb}\n",
        )
        self._init_queue(systems)
        env = self._env(FAKE_GMX_FAIL_FILE=self.flag)
        with open(self.flag, "w") as fh:
            fh.write("fail\n")
        wlog = os.path.join(self.ws.root, "watch.log")
        proc, fh = _start_watch(
            env, wlog, "--queue", self.qp, "--interval", "0.01",
            "--repair-timeout", "0.01", "--max-wait", "2",
        )
        try:
            rc = proc.wait(timeout=120)
        finally:
            fh.close()
        self.assertEqual(rc, 2)
        data = _load_json(self.qp)
        self.assertEqual(data["items"]["protA"]["status"], "repair-timeout")

    def test_exec_stop_exit130(self):
        systems = self.ws.write(
            "systems.yaml",
            "systems:\n"
            "  - name: protA\n"
            "    protein: {file: inputs/protein_A.pdb}\n",
        )
        self._init_queue(systems)
        env = self._env(FAKE_GMX_SLOW_MD="1")
        wlog = os.path.join(self.ws.root, "watch.log")
        proc, fh = _start_watch(
            env, wlog, "--queue", self.qp, "--interval", "0.01"
        )
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                data = _load_json(self.qp)
                if data["items"]["protA"]["status"] == "running":
                    rs_path = os.path.join(self.outbase, "protA", "run_status.json")
                    if os.path.isfile(rs_path):
                        rs = _load_json(rs_path)
                        if rs["systems"]["protA"]["steps"]["md"]["status"] == "running":
                            break
                time.sleep(0.3)
            r = _run_cli(env, "ctl", "exec", "stop", "-q", self.qp)
            self.assertEqual(r.returncode, 0, r.stderr)
            rc = proc.wait(timeout=120)
        finally:
            fh.close()
        self.assertEqual(rc, 130)

    def test_intervention_checkpoint_resume(self):
        systems = self.ws.write(
            "systems.yaml",
            "systems:\n"
            "  - name: protA\n"
            "    protein: {file: inputs/protein_A.pdb}\n",
        )
        self._init_queue(systems)
        env = self._env(FAKE_GMX_SLOW_MD="1")
        wlog = os.path.join(self.ws.root, "watch.log")
        proc, fh = _start_watch(
            env, wlog, "--queue", self.qp, "--interval", "0.01", "--repair-timeout", "1"
        )
        try:
            _wait_queue(
                self.qp,
                lambda d: d["items"]["protA"]["status"] == "running",
            )
            # wait until the md step itself is running
            md_dir = os.path.join(self.outbase, "protA", "protA")
            deadline = time.time() + 60
            while time.time() < deadline:
                rs_path = os.path.join(self.outbase, "protA", "run_status.json")
                if os.path.isfile(rs_path):
                    rs = _load_json(rs_path)
                    if rs["systems"]["protA"]["steps"]["md"]["status"] == "running":
                        break
                time.sleep(0.3)
            r = _run_cli(env, "ctl", "retry", "-q", self.qp, "protA")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("queued", r.stdout)
            rc = proc.wait(timeout=120)
        finally:
            fh.close()
        self.assertEqual(rc, 0)
        data = _load_json(self.qp)
        self.assertEqual(data["items"]["protA"]["status"], "done")
        self.assertEqual(data["items"]["protA"]["attempts"], 2)
        resume = glob.glob(
            os.path.join(self.outbase, "protA", "protA", "*_md", ".resume", "*.cpt")
        )
        self.assertEqual(len(resume), 1)
        rs = _load_json(os.path.join(self.outbase, "protA", "run_status.json"))
        md_cmds = rs["systems"]["protA"]["steps"]["md"].get("commands") or []
        self.assertTrue(
            any("-cpi" in c for c in md_cmds),
            "第二次运行应带 -cpi 续跑: %s" % md_cmds,
        )

    def test_single_run_manual_check(self):
        wf = _workflow(
            self.ws,
            extra_steps=(
                "  - step: manual_check\n"
                "    params:\n"
                "      message: 请确认\n"
            ),
        )
        systems = self.ws.write(
            "systems.yaml",
            "work_dir: ./result\n"
            "systems:\n"
            "  - name: protA\n"
            "    protein: {file: inputs/protein_A.pdb}\n",
        )
        env = self._env()
        r = _run_cli(env, "run", "-w", wf, "-s", systems)
        self.assertEqual(r.returncode, 0, r.stderr)
        run_dir = os.path.join(self.ws.root, "result")
        wlog = os.path.join(self.ws.root, "watch.log")
        proc, fh = _start_watch(
            env, wlog, run_dir, "--interval", "0.01", "--max-wait", "1"
        )
        try:
            time.sleep(2.0)
            r = _run_cli(env, "skip", run_dir, "protA", "manual_check", "--reason", "ok")
            self.assertEqual(r.returncode, 0, r.stderr)
            rc = proc.wait(timeout=120)
        finally:
            fh.close()
        self.assertEqual(rc, 0)
        rs = _load_json(os.path.join(run_dir, "run_status.json"))
        self.assertEqual(rs["systems"]["protA"]["status"], "done")

    def test_hold_release(self):
        systems = self.ws.write(
            "systems.yaml",
            "systems:\n"
            "  - name: protA\n"
            "    protein: {file: inputs/protein_A.pdb}\n"
            "  - name: protB\n"
            "    protein: {file: inputs/protein_B.pdb}\n",
        )
        self._init_queue(systems, concurrency=1)
        env = self._env()
        r = _run_cli(env, "ctl", "queue", "hold", "-q", self.qp, "--system", "protB")
        self.assertEqual(r.returncode, 0, r.stderr)
        wlog = os.path.join(self.ws.root, "watch.log")
        proc, fh = _start_watch(
            env, wlog, "--queue", self.qp, "--interval", "0.01", "--max-wait", "2"
        )
        try:
            _wait_queue(
                self.qp,
                lambda d: d["items"]["protA"]["status"] == "done"
                and d["items"]["protB"]["status"] == "held",
            )
            r = _run_cli(env, "ctl", "queue", "release", "-q", self.qp, "--system", "protB")
            self.assertEqual(r.returncode, 0, r.stderr)
            rc = proc.wait(timeout=120)
        finally:
            fh.close()
        self.assertEqual(rc, 0)
        data = _load_json(self.qp)
        self.assertEqual(data["items"]["protB"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
