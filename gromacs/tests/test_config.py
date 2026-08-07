"""Config parsing / validation unit tests."""

from __future__ import annotations

import unittest

from mdkit.config import load_systems, load_workflow
from mdkit.exceptions import ConfigError
from mdkit.runner import effective_params
from mdkit.steps import load_steps

from tests.helpers import TempWorkspace


WORKFLOW = """
name: test
failure_policy: continue
layout: per_step
steps:
  - step: env_check
  - step: protein_prep
    params:
      force_field: amber99sb-ildn
      water_model: tip3p
  - step: md
"""


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    def test_workflow_ok(self):
        path = self.ws.write("workflow.yaml", WORKFLOW)
        wf = load_workflow(path)
        self.assertEqual(wf.name, "test")
        self.assertEqual([s.name for s in wf.steps], ["env_check", "protein_prep", "md"])
        self.assertEqual(wf.failure_policy, "continue")

    def test_bad_failure_policy(self):
        path = self.ws.write("workflow.yaml", WORKFLOW.replace("continue", "explode"))
        with self.assertRaises(ConfigError):
            load_workflow(path)

    def test_bad_layout(self):
        path = self.ws.write(
            "workflow.yaml", WORKFLOW.replace("per_step", "spiral")
        )
        with self.assertRaises(ConfigError):
            load_workflow(path)

    def test_stage_name_parsed(self):
        path = self.ws.write(
            "workflow.yaml",
            WORKFLOW.replace("layout: per_step", "layout: per_step\nstage_name: .work"),
        )
        wf = load_workflow(path)
        self.assertEqual(wf.stage_name, ".work")

    def test_stage_name_rejects_path(self):
        path = self.ws.write(
            "workflow.yaml",
            WORKFLOW.replace(
                "layout: per_step", "layout: per_step\nstage_name: /tmp/x"
            ),
        )
        with self.assertRaises(ConfigError):
            load_workflow(path)

    def test_unknown_step_in_workflow(self):
        path = self.ws.write(
            "workflow.yaml",
            WORKFLOW.replace("env_check", "no_such_step"),
        )
        wf = load_workflow(path)
        from mdkit.runner import Runner

        class FakeCfg:
            systems = []
            path = self.ws.write("systems.yaml", "systems:\n")

        with self.assertRaises(ConfigError):
            Runner(wf, FakeCfg(), "/tmp/x", log=None)

    def test_systems_ok_protein_only(self):
        self.ws.add_protein()
        path = self.ws.systems_yaml(
            "  - name: prot\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands: []\n"
        )
        cfg = load_systems(path)
        self.assertEqual(len(cfg.systems), 1)
        self.assertFalse(cfg.systems[0].has_ligands)

    def test_systems_multiligand_multimer(self):
        self.ws.add_protein("a.pdb")
        self.ws.add_protein("b.pdb")
        self.ws.add_ligand("l1.sdf")
        self.ws.add_ligand("l2.sdf")
        path = self.ws.systems_yaml(
            "  - name: complex\n"
            "    protein:\n      chains:\n        - inputs/a.pdb\n"
            "        - inputs/b.pdb\n"
            "    ligands:\n"
            "      - name: L1\n        file: inputs/l1.sdf\n        charge: 0\n"
            "      - name: L2\n        file: inputs/l2.sdf\n        charge: -1\n"
            "        count: 2\n"
        )
        cfg = load_systems(path)
        system = cfg.systems[0]
        self.assertTrue(system.protein.is_multimer)
        self.assertEqual(len(system.ligands), 2)
        self.assertEqual(system.ligands[1].count, 2)
        self.assertEqual(system.ligands[1].charge, -1)

    def test_system_name_with_slash_rejected(self):
        path = self.ws.write(
            "systems.yaml",
            "systems:\n  - name: a/b\n    protein:\n      file: x.pdb\n",
        )
        with self.assertRaises(ConfigError):
            load_systems(path)

    def test_manual_ligand_requires_itp_gro(self):
        path = self.ws.write(
            "systems.yaml",
            "systems:\n  - name: c\n    protein:\n      file: x.pdb\n"
            "    ligands:\n      - name: M\n        method: manual\n        file: y.sdf\n",
        )
        with self.assertRaises(ConfigError):
            load_systems(path)

    def test_effective_params_merge_and_on_failure(self):
        self.ws.add_protein()
        wf = load_workflow(self.ws.write("workflow.yaml", WORKFLOW))
        syscfg = load_systems(
            self.ws.systems_yaml(
                "  - name: prot\n    protein:\n      file: inputs/protein_A.pdb\n"
                "    ligands: []\n"
                "    overrides:\n      md:\n        nt: 4\n"
            )
        )
        steps = load_steps()
        spec = wf.step_by_name("md")
        params = effective_params(wf, steps, syscfg.systems[0], spec)
        self.assertEqual(params["nt"], 4)
        self.assertEqual(params["on_failure"], "auto")

    def test_unknown_param_rejected(self):
        self.ws.add_protein()
        wf = load_workflow(
            self.ws.write(
                "workflow.yaml",
                WORKFLOW.replace(
                    "  - step: md",
                    "  - step: md\n    params:\n      bogus_param: 1",
                ),
            )
        )
        syscfg = load_systems(
            self.ws.systems_yaml(
                "  - name: prot\n    protein:\n      file: inputs/protein_A.pdb\n"
                "    ligands: []\n"
            )
        )
        steps = load_steps()
        with self.assertRaises(ConfigError):
            effective_params(wf, steps, syscfg.systems[0], wf.step_by_name("md"))


if __name__ == "__main__":
    unittest.main()
