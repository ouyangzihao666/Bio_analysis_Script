"""Ligand-related integration tests: mol2 / multi-molecule / PDB residue."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from mdkit.config import load_systems, load_workflow
from mdkit.exceptions import ConfigError
from mdkit.monitor import RunState
from mdkit.runner import Runner

from tests.helpers import MULTI_MOL2, TINY_PDB, TempWorkspace, make_fake_gmx, make_fake_ligand_tools


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
  - step: protein_prep
  - step: ligand_prep
  - step: complex_merge
  - step: box
  - step: solvate
  - step: ions
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
        self.env = dict(os.environ)
        self.env["PATH"] = (
            self.fake_bin
            + os.pathsep
            + self.lig_bin
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
        lig_dir = os.path.join(run_dir, "comp", "03_ligand_prep")
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "FDME_GMX.itp")))
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "BDO_GMX.itp")))
        with open(os.path.join(lig_dir, "FDME_GMX.itp"), encoding="utf-8") as fh:
            self.assertIn("FDME", fh.read())
        ndx = os.path.join(run_dir, "comp", "12_index", "comp.ndx")
        with open(ndx, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("[ Ligand_FDME ]", content)
        self.assertIn("[ Ligand_BDO ]", content)

    def test_multi_molecule_mol2_single_file(self):
        self.ws.write("inputs/two.mol2", MULTI_MOL2)
        code, data = self._run(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n"
            "      - {file: inputs/two.mol2, names: [FDME, BDO], charge: 0}\n"
        )
        self.assertEqual(code, 0)
        run_dir = os.path.join(self.ws.root, "result")
        lig_dir = os.path.join(run_dir, "comp", "03_ligand_prep")
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "FDME_GMX.itp")))
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "BDO_GMX.itp")))

    def test_ligand_extracted_from_pdb_residue(self):
        merged_pdb = (
            TINY_PDB.replace("END\n", "")
            + "HETATM 1933  C   UNK     1       2.661  -7.976   3.560  0.00  0.00           C\n"
            + "HETATM 1934  O   UNK     1       3.386  -5.586   4.460  0.00  0.00           O\n"
            + "END\n"
        )
        self.ws.write("inputs/merged.pdb", merged_pdb)
        code, data = self._run(
            "  - name: comp\n    protein:\n      file: inputs/merged.pdb\n"
            "    ligands:\n"
            "      - {name: BHET, file: inputs/merged.pdb, residue: UNK, charge: 0}\n"
        )
        self.assertEqual(code, 0)
        run_dir = os.path.join(self.ws.root, "result")
        self.assertTrue(
            os.path.isfile(
                os.path.join(run_dir, "comp", "03_ligand_prep", "BHET_GMX.itp")
            )
        )

    def test_same_name_molecules_get_suffix_and_note(self):
        same = MULTI_MOL2.replace("BDO0", "FME0")
        self.ws.write("inputs/two.mol2", same)
        systems_path = self.ws.systems_yaml(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n      - {file: inputs/two.mol2, charge: 0}\n"
        )
        cfg = load_systems(systems_path)
        names = [l.name for l in cfg.systems[0].ligands]
        self.assertEqual(names, ["FME", "FME_2"])
        self.assertTrue(cfg.systems[0].review_notes)

    def test_names_count_mismatch_rejected(self):
        self.ws.write("inputs/two.mol2", MULTI_MOL2)
        systems_path = self.ws.systems_yaml(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n      - {file: inputs/two.mol2, names: [ONLY], charge: 0}\n"
        )
        with self.assertRaises(ConfigError):
            load_systems(systems_path)

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
