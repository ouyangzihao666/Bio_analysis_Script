"""CLI status output filtering tests."""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from mdkit.cli import cmd_status
from mdkit.monitor import init_status

from tests.helpers import TempWorkspace


class StatusFilterTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()
        self.run_dir = os.path.join(self.ws.root, "run")
        os.makedirs(self.run_dir)

    def tearDown(self):
        self.ws.cleanup()

    def _make_status(self):
        data = init_status(
            self.run_dir,
            "t",
            "wf.yaml",
            "systems.yaml",
            [SimpleNamespace(name="caseA"), SimpleNamespace(name="caseB")],
            ["env_check", "md"],
        )
        data["systems"]["caseA"]["steps"]["env_check"]["status"] = "done"
        data["systems"]["caseA"]["steps"]["md"]["status"] = "running"
        data["systems"]["caseA"]["status"] = "running"
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


if __name__ == "__main__":
    unittest.main()
