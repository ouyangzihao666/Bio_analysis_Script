"""Ligand-related integration tests: split steps + parameterization."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from mdkit.config import load_systems, load_workflow
from mdkit.exceptions import ConfigError
from mdkit.monitor import RunState
from mdkit.runner import Runner

from tests.helpers import (
    MULTI_MOL2,
    TINY_PDB,
    TempWorkspace,
    make_fake_gmx,
    make_fake_ligand_tools,
    make_fake_pymol,
)


def _mdp_dir():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "configs",
        "mdp",
    )


COMPLEX_WORKFLOW = """name: test-complex
failure_policy: continue
layout: per_step
mdp_dir: %s
steps:
  - step: env_check
    params:
      tools: [obabel, antechamber, acpype]
  - step: split_complex
    params:
      on_failure: pause
  - step: split_ligand
    params:
      on_failure: pause
  - step: protein_prep
  - step: ligand_prep
  - step: complex_merge
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


class LigandIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()
        self.ws.write("inputs/protein_A.pdb", TINY_PDB)
        self.fake_bin = make_fake_gmx()
        self.lig_bin = make_fake_ligand_tools()
        self.pymol_bin = make_fake_pymol()
        self.env = dict(os.environ)
        self.env["PATH"] = (
            self.fake_bin
            + os.pathsep
            + self.lig_bin
            + os.pathsep
            + self.pymol_bin
            + os.pathsep
            + self.env.get("PATH", "")
        )
        self.workflow_path = self.ws.write(
            "workflow.yaml", COMPLEX_WORKFLOW % _mdp_dir()
        )

    def tearDown(self):
        self.ws.cleanup()

    def _run(self, systems_block):
        systems_path = self.ws.systems_yaml(systems_block)
        workflow = load_workflow(self.workflow_path)
        cfg = load_systems(systems_path)
        work_dir = cfg.resolve_work_dir()
        with patch.dict(os.environ, self.env):
            code = Runner(workflow, cfg, work_dir, log=None).run()
        return code, RunState(work_dir).load()

    def test_two_split_mol2_ligands(self):
        blocks = MULTI_MOL2.split("@<TRIPOS>MOLECULE")
        self.ws.write(
            "inputs/FDME.mol2",
            "@<TRIPOS>MOLECULE" + blocks[1],
        )
        self.ws.write(
            "inputs/BDO.mol2",
            "@<TRIPOS>MOLECULE" + blocks[2],
        )
        code, data = self._run(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n"
            "      - {name: FDME, file: inputs/FDME.mol2, charge: 0}\n"
            "      - {name: BDO, file: inputs/BDO.mol2, charge: 0}\n"
        )
        self.assertEqual(code, 0)
        steps = data["systems"]["comp"]["steps"]
        self.assertEqual(steps["complex_merge"]["status"], "done")
        run_dir = os.path.join(self.ws.root, "result")
        lig_dir = os.path.join(run_dir, "comp", "05_ligand_prep")
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "FDME_GMX.itp")))
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "BDO_GMX.itp")))
        with open(os.path.join(lig_dir, "FDME_GMX.itp"), encoding="utf-8") as fh:
            self.assertIn("FDME", fh.read())
        ndx = os.path.join(run_dir, "comp", "14_index", "comp.ndx")
        with open(ndx, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("[ Ligand_FDME ]", content)
        self.assertIn("[ Ligand_BDO ]", content)

    def test_multi_molecule_mol2_single_file(self):
        self.ws.write("inputs/two.mol2", MULTI_MOL2)
        code, data = self._run(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n"
            "      - {file: inputs/two.mol2, names: [FME, BDO], charge: 0}\n"
        )
        self.assertEqual(code, 0)
        run_dir = os.path.join(self.ws.root, "result")
        lig_dir = os.path.join(run_dir, "comp", "05_ligand_prep")
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "FME_GMX.itp")))
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "BDO_GMX.itp")))

    def test_complex_split_by_pymol(self):
        merged_pdb = (
            TINY_PDB.replace("END\n", "")
            + "HETATM 1933  C   UNK A 501       2.661  -7.976   3.560  0.00  0.00           C\n"
            + "HETATM 1934  O   UNK A 501       3.386  -5.586   4.460  0.00  0.00           O\n"
            + "END\n"
        )
        self.ws.write("inputs/merged.pdb", merged_pdb)
        code, data = self._run(
            "  - name: comp\n"
            "    complex:\n"
            "      file: inputs/merged.pdb\n"
            "      ligands:\n"
            "        - {name: UNK, charge: 0}\n"
        )
        self.assertEqual(code, 0)
        run_dir = os.path.join(self.ws.root, "result")
        self.assertTrue(
            os.path.isfile(
                os.path.join(run_dir, "comp", "05_ligand_prep", "UNK_GMX.itp")
            )
        )
        steps = data["systems"]["comp"]["steps"]
        self.assertEqual(steps["split_complex"]["status"], "done")
        self.assertEqual(steps["ligand_prep"]["status"], "done")

    def test_multi_molecule_without_names_left_unexpanded(self):
        self.ws.write("inputs/two.mol2", MULTI_MOL2)
        systems_path = self.ws.systems_yaml(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n      - {file: inputs/two.mol2, charge: 0}\n"
        )
        cfg = load_systems(systems_path)
        names = [l.name for l in cfg.systems[0].ligands]
        self.assertEqual(names, ["two"])
        self.assertTrue(cfg.systems[0].review_notes)
        code, data = self._run(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n      - {file: inputs/two.mol2, charge: 0}\n"
        )
        self.assertEqual(code, 2)
        st = data["systems"]["comp"]["steps"]["split_ligand"]
        self.assertEqual(st["status"], "awaiting_input")
        self.assertIn("FME", st["note"])
        self.assertIn("BDO", st["note"])

    def test_names_count_mismatch_reports_molecules_and_pauses(self):
        self.ws.write("inputs/two.mol2", MULTI_MOL2)
        systems_path = self.ws.systems_yaml(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n      - {file: inputs/two.mol2, names: [ONLY], charge: 0}\n"
        )
        cfg = load_systems(systems_path)
        self.assertEqual(len(cfg.systems[0].ligands), 1)
        code, data = self._run(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n      - {file: inputs/two.mol2, names: [ONLY], charge: 0}\n"
        )
        self.assertEqual(code, 2)
        st = data["systems"]["comp"]["steps"]["split_ligand"]
        self.assertEqual(st["status"], "awaiting_input")
        self.assertIn("FME", st["note"])

    def test_ligand_name_too_long_rejected(self):
        self.ws.write("inputs/FDME.mol2", "x")
        systems_path = self.ws.systems_yaml(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n      - {name: TOOLONGNAME, file: inputs/FDME.mol2}\n"
        )
        with self.assertRaises(ConfigError):
            load_systems(systems_path)


if __name__ == "__main__":
    unittest.main()
