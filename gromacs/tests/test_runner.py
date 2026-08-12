"""Runner integration tests using a fake gmx."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from mdkit.config import load_systems, load_workflow
from mdkit.monitor import RunState
from mdkit.progress import step_progress
from mdkit.runner import Runner, cmd_clean, cmd_retry, cmd_skip

from tests.helpers import TempWorkspace, make_fake_gmx, with_fake_path


def _mdp_dir():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "configs",
        "mdp",
    )


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()
        self.ws.add_protein()
        self.ws.add_protein("protein_B.pdb")
        self.fake_bin = make_fake_gmx()
        self.env = with_fake_path(self.fake_bin)
        self.workflow_path = self.ws.write(
            "workflow.yaml",
            """name: test-protein
failure_policy: continue
layout: per_step
mdp_dir: %s
steps:
  - step: env_check
  - step: protein_prep
  - step: box
  - step: solvate
  - step: ions
    params:
      positive_ion: NA
      negative_ion: CL
  - step: em
  - step: nvt
  - step: npt
  - step: md
  - step: index
  - step: traj_correct
"""
            % _mdp_dir(),
        )

    def tearDown(self):
        self.ws.cleanup()

    def _systems(self, block):
        return self.ws.systems_yaml(block)

    def _run(self, systems_path, **kw):
        workflow = load_workflow(self.workflow_path)
        systems_cfg = load_systems(systems_path)
        work_dir = systems_cfg.resolve_work_dir()
        runner = Runner(workflow, systems_cfg, work_dir, log=None, **kw)
        with patch.dict(os.environ, self.env):
            return runner.run(), RunState(work_dir).load()

    def _one_system(self):
        return self._systems(
            "  - name: protA\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands: []\n"
        )

    def test_full_run_and_skip_on_second_run(self):
        systems_path = self._one_system()
        code, data = self._run(systems_path)
        self.assertEqual(code, 0)
        sys_entry = data["systems"]["protA"]
        self.assertEqual(sys_entry["status"], "done")
        for name in (
            "env_check", "protein_prep", "box", "solvate", "ions",
            "em", "nvt", "npt", "md", "index", "traj_correct",
        ):
            self.assertEqual(sys_entry["steps"][name]["status"], "done", name)
        run_dir = os.path.join(self.ws.root, "result")
        self.assertTrue(
            os.path.isfile(
                os.path.join(run_dir, "protA", "09_md", "protA_md.xtc")
            )
        )
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    run_dir, "protA", "11_traj_correct", "protA_md_corrected.xtc"
                )
            )
        )
        md_gro = os.path.join(run_dir, "protA", "09_md", "protA_md.gro")
        before = os.path.getmtime(md_gro)
        code2, data2 = self._run(systems_path)
        self.assertEqual(code2, 0)
        self.assertEqual(os.path.getmtime(md_gro), before)
        self.assertEqual(data2["systems"]["protA"]["status"], "done")

    def test_mdp_param_change_reruns_step(self):
        systems_path = self._one_system()
        code, data = self._run(systems_path)
        self.assertEqual(code, 0)
        old_sig = data["systems"]["protA"]["steps"]["em"]["signature"]
        # Change an em mdp parameter -> signature changes -> rerun.
        self.ws.write(
            "workflow.yaml",
            open(self.workflow_path, encoding="utf-8").read().replace(
                "  - step: em",
                "  - step: em\n    params:\n      mdp_overrides:\n        nsteps: 1000",
            ),
        )
        code2, data2 = self._run(systems_path)
        self.assertEqual(code2, 0)
        new_sig = data2["systems"]["protA"]["steps"]["em"]["signature"]
        self.assertNotEqual(new_sig, old_sig)
        self.assertEqual(data2["systems"]["protA"]["steps"]["em"]["status"], "done")

    def test_mdrun_failure_then_retry(self):
        systems_path = self._one_system()
        with patch.dict(os.environ, {**self.env, "FAKE_GMX_FAIL": "mdrun"}):
            code, data = self._run(systems_path)
        self.assertEqual(code, 2)
        steps = data["systems"]["protA"]["steps"]
        self.assertEqual(steps["md"]["status"], "failed")
        self.assertEqual(steps["index"]["status"], "skipped")
        self.assertEqual(data["systems"]["protA"]["status"], "failed")
        run_dir = os.path.join(self.ws.root, "result")
        cmd_retry(run_dir, "protA", "md")
        code2, data2 = self._run(systems_path)
        self.assertEqual(code2, 0)
        self.assertEqual(data2["systems"]["protA"]["status"], "done")
        self.assertEqual(data2["systems"]["protA"]["steps"]["md"]["status"], "done")

    def test_mdrun_live_tee_reports_remaining_time(self):
        systems_path = self._one_system()
        workflow = load_workflow(self.workflow_path)
        systems_cfg = load_systems(systems_path)
        work_dir = systems_cfg.resolve_work_dir()
        env = {**self.env, "FAKE_GMX_SLOW_MD": "1"}
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mdkit",
                "run",
                "-w",
                self.workflow_path,
                "-s",
                systems_path,
            ],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            stage_out = os.path.join(
                work_dir,
                "protA",
                "09_md",
                ".stage",
                "protA_md.mdrun.out",
            )
            deadline = time.time() + 30
            while time.time() < deadline and not os.path.isfile(stage_out):
                time.sleep(0.1)
            self.assertTrue(
                os.path.isfile(stage_out), "mdrun tee 文件未生成: %s" % stage_out
            )
            with open(stage_out, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("remaining wall clock time", content)

            prog = None
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                prog = step_progress(workflow, work_dir, "protA", "md")
                if prog and prog.get("remaining"):
                    break
                time.sleep(0.1)
            self.assertIsNotNone(prog, "step_progress 未返回 remaining")
            self.assertIn("remaining wall clock time", prog["remaining"])
            self.assertIn("step 100", prog["remaining"])
        finally:
            rc = proc.wait(timeout=60)
        self.assertEqual(rc, 0, "mdkit run 子进程非零退出: %s" % rc)

    def test_manual_check_pause_and_skip(self):
        self.ws.write(
            "workflow.yaml",
            open(self.workflow_path, encoding="utf-8").read().replace(
                "  - step: md",
                "  - step: manual_check\n    params:\n      message: 请确认\n"
                "  - step: md",
            ),
        )
        systems_path = self._one_system()
        run_dir = os.path.join(self.ws.root, "result")
        code, data = self._run(systems_path)
        # First pass stops at manual_check (awaiting), which is not a failure.
        self.assertEqual(code, 0)
        steps = data["systems"]["protA"]["steps"]
        self.assertEqual(steps["manual_check"]["status"], "awaiting_input")
        self.assertEqual(data["systems"]["protA"]["status"], "paused")
        cmd_skip(run_dir, "protA", "manual_check", reason="确认无误")
        code2, data2 = self._run(systems_path)
        self.assertEqual(code2, 0)
        self.assertEqual(data2["systems"]["protA"]["steps"]["manual_check"]["status"], "skipped")
        self.assertEqual(data2["systems"]["protA"]["steps"]["md"]["status"], "done")

    def test_rollback_and_clean(self):
        systems_path = self._one_system()
        code, data = self._run(systems_path)
        self.assertEqual(code, 0)
        run_dir = os.path.join(self.ws.root, "result")
        md_dir = os.path.join(run_dir, "protA", "09_md")
        tpr = os.path.join(md_dir, "protA_md.tpr")
        self.assertTrue(os.path.isfile(tpr))
        cmd_retry(run_dir, "protA", "md")
        result = cmd_clean(run_dir, "protA", from_step="md", yes=True)
        self.assertFalse(os.path.isfile(tpr))
        self.assertTrue(result["confirmed"])

    def test_plan_does_not_create_dirs(self):
        from mdkit.cli import main

        systems_path = self._one_system()
        out = StringIO()
        with patch.dict(os.environ, self.env):
            with redirect_stdout(out):
                code = main(
                    [
                        "plan",
                        "-w",
                        self.workflow_path,
                        "-s",
                        systems_path,
                    ]
                )
        self.assertEqual(code, 0)
        self.assertFalse(
            os.path.exists(os.path.join(self.ws.root, "result"))
        )


if __name__ == "__main__":
    unittest.main()
