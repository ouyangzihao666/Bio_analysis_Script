"""File registry and convention resolution tests."""

from __future__ import annotations

import os
import unittest

from mdkit.registry import FileRegistry

from tests.helpers import TempWorkspace


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    class FakeSystem:
        def __init__(self, name):
            self.name = name

    def test_convention_glob(self):
        run = os.path.join(self.ws.root, "result")
        tpr = self.ws.write(
            "result/prot/10_md_production/prot_md.tpr", "x"
        )
        registry = FileRegistry(run, self.FakeSystem("prot"))
        self.assertEqual(registry.get("md_tpr"), tpr)

    def test_flat_fallback(self):
        run = os.path.join(self.ws.root, "result")
        xtc = self.ws.write("result/prot/prot_md.xtc", "x")
        registry = FileRegistry(run, self.FakeSystem("prot"))
        self.assertEqual(registry.get("md_xtc"), xtc)

    def test_set_and_require(self):
        run = os.path.join(self.ws.root, "result")
        registry = FileRegistry(run, self.FakeSystem("prot"))
        registry.set("foo", self.ws.write("result/prot/foo.dat", "x"))
        self.assertEqual(registry.require("foo"), os.path.join(self.ws.root, "result/prot/foo.dat"))
        self.assertIsNone(registry.get("bar"))

    def test_require_traj_fallback(self):
        run = os.path.join(self.ws.root, "result")
        registry = FileRegistry(run, self.FakeSystem("prot"))
        corrected = self.ws.write(
            "result/prot/11_traj_correct/prot_md_corrected.xtc", "x"
        )
        self.assertEqual(registry.require_traj(), corrected)
        raw = self.ws.write("result/prot/10_md_production/prot_md.xtc", "x")
        os.remove(corrected)
        self.assertEqual(registry.require_traj(), raw)


if __name__ == "__main__":
    unittest.main()
