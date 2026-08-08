"""CLI status output filtering tests."""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from mdkit.cli import _step_progress, cmd_status
from mdkit.config import load_workflow
from mdkit.monitor import RunState, init_status

from tests.helpers import TempWorkspace


class StatusFilterTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()
        self.run_dir = os.path.join(self.ws.root, "run")
        os.makedirs(self.run_dir)

    def tearDown(self):
        self.ws.cleanup()

    def _make_status(self):
        wf_path = self.ws.write(
            "workflow.yaml",
            "name: t\nsteps:\n  - step: md\n  - step: env_check\n",
        )
        data = init_status(
            self.run_dir,
            "t",
            wf_path,
            "systems.yaml",
            [SimpleNamespace(name="caseA"), SimpleNamespace(name="caseB")],
            ["env_check", "md"],
        )
        data["run"]["workflow"] = wf_path
        data["systems"]["caseA"]["steps"]["env_check"]["status"] = "done"
        data["systems"]["caseA"]["steps"]["md"]["status"] = "running"
        data["systems"]["caseA"]["status"] = "running"
        RunState(self.run_dir).save(data)
        return data

    def _run_status(self, system=None):
        self._make_status()
        out = io.StringIO()
        args = SimpleNamespace(run_dir=self.run_dir, system=[system] if system else None, json=False)
        with redirect_stdout(out):
            code = cmd_status(args, None)
        return code, out.getvalue()

    def test_pending_systems_hidden_by_default(self):
        code, text = self._run_status()
        self.assertEqual(code, 0)
        self.assertIn("[caseA]", text)
        self.assertNotIn("[caseB]", text)
        self.assertIn("另有 1 个体系", text)

    def test_system_filter_shows_requested_system(self):
        code, text = self._run_status(system="caseB")
        self.assertEqual(code, 0)
        self.assertIn("[caseB]", text)
        self.assertNotIn("[caseA]", text)
        self.assertNotIn("另有", text)

    def test_steps_displayed_in_workflow_order(self):
        code, text = self._run_status()
        self.assertEqual(code, 0)
        md_pos = text.index("md ")
        env_pos = text.index("env_check")
        self.assertLess(md_pos, env_pos)

    def test_status_discovers_parent_run_dirs(self):
        wf_path = self.ws.write(
            "workflow.yaml",
            "name: t\nsteps:\n  - step: env_check\n  - step: md\n",
        )
        parent = os.path.join(self.ws.root, "bench", "test1")
        for name in ("sysA", "sysB"):
            d = os.path.join(parent, name)
            os.makedirs(d)
            data = init_status(
                d,
                "t",
                wf_path,
                "systems.yaml",
                [SimpleNamespace(name=name)],
                ["env_check", "md"],
            )
            data["systems"][name]["steps"]["env_check"]["status"] = "done"
            data["systems"][name]["steps"]["md"]["status"] = "running"
            data["systems"][name]["status"] = "running"
            RunState(d).save(data)
        out = io.StringIO()
        args = SimpleNamespace(run_dir=os.path.join(self.ws.root, "bench", "test1"), system=None, json=False)
        with redirect_stdout(out):
            code = cmd_status(args, None)
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("sysA", text)
        self.assertIn("sysB", text)

    def test_status_json_parent_wraps_runs(self):
        wf_path = self.ws.write(
            "workflow.yaml",
            "name: t\nsteps:\n  - step: env_check\n  - step: md\n",
        )
        parent = os.path.join(self.ws.root, "bench2")
        for name in ("sysA", "sysB"):
            d = os.path.join(parent, name)
            os.makedirs(d)
            data = init_status(
                d,
                "t",
                wf_path,
                "systems.yaml",
                [SimpleNamespace(name=name)],
                ["env_check", "md"],
            )
            RunState(d).save(data)
        out = io.StringIO()
        args = SimpleNamespace(run_dir=parent, system=None, json=True)
        with redirect_stdout(out):
            code = cmd_status(args, None)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertIn("run_dirs", payload)

    def test_step_progress_parses_mdrun_log(self):
        wf = load_workflow(
            self.ws.write(
                "workflow.yaml",
                "name: t\nsteps:\n  - step: md\n",
            )
        )
        # step_dir_for -> <run>/caseA/01_md
        step_dir = os.path.join(self.run_dir, "caseA", "01_md", ".stage")
        os.makedirs(step_dir)
        with open(os.path.join(step_dir, "caseA_md.log"), "w") as fh:
            fh.write(
                "Step Time\n"
                "   500        1.000\n"
                "  1000        2.000\n"
                "Finished mdrun\n"
            )
        with open(os.path.join(step_dir, "md.mdp"), "w") as fh:
            fh.write("nsteps = 5000000\n")
        prog = _step_progress(wf, self.run_dir, "caseA", "md")
        self.assertEqual(prog["step"], 1000)
        self.assertEqual(prog["time_ps"], 2.0)
        self.assertEqual(prog["nsteps"], 5000000)
        self.assertAlmostEqual(prog["percent"], 0.02)

    def test_plan_prints_commands(self):
        from mdkit.cli import main

        self.ws.add_protein()
        wf = self.ws.write(
            "workflow.yaml",
            "name: t\nsteps:\n  - step: protein_prep\n  - step: box\n",
        )
        systems = self.ws.systems_yaml(
            "  - name: protA\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands: []\n"
        )
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["plan", "-w", wf, "-s", systems, "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        steps = data["systems"][0]["steps"]
        pdb2gmx_cmd = [
            c for c in steps[0]["commands"] if "pdb2gmx" in c
        ]
        editconf_cmd = [
            c for c in steps[1]["commands"] if "editconf" in c
        ]
        self.assertTrue(pdb2gmx_cmd)
        self.assertTrue(editconf_cmd)


if __name__ == "__main__":
    unittest.main()
