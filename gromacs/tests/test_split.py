"""Split steps / choice flow / complex merge / precheck regression tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from mdkit import gro, ligsplit
from mdkit.cli import build_parser
from mdkit.config import load_systems, load_workflow
from mdkit.exceptions import ConfigError
from mdkit.monitor import RunState
from mdkit.runner import Runner, cmd_retry
from mdkit.steps.analysis import RmsdStep
from mdkit.steps.simulation import EmStep

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


SPLIT_WORKFLOW = """name: split
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
  - step: __LIGSTEP__
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


class SplitTests(unittest.TestCase):
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

    def tearDown(self):
        self.ws.cleanup()

    def _workflow(self, lig_step="split_ligand"):
        return self.ws.write(
            "workflow.yaml",
            (SPLIT_WORKFLOW % _mdp_dir()).replace("__LIGSTEP__", lig_step),
        )

    def _run(self, systems_block, lig_step="split_ligand"):
        systems_path = self.ws.systems_yaml(systems_block)
        workflow = load_workflow(self._workflow(lig_step))
        cfg = load_systems(systems_path)
        work_dir = cfg.resolve_work_dir()
        with patch.dict(os.environ, self.env):
            code = Runner(workflow, cfg, work_dir, log=None).run()
        return code, RunState(work_dir).load(), work_dir

    def test_match_assignments_subset_and_ambiguity(self):
        molecules = [
            {"name": "FME", "index": 0},
            {"name": "FME", "index": 1},
            {"name": "BDO", "index": 2},
        ]
        status, result = ligsplit.match_assignments(["FME", "BDO"], molecules)
        self.assertEqual(status, "ambiguous")
        name, candidates = result
        self.assertEqual(name, "FME")
        self.assertEqual([c["key"] for c in candidates], ["1", "2"])
        status, result = ligsplit.match_assignments(
            ["FME", "BDO"], molecules, pin=("FME", 1)
        )
        self.assertEqual(status, "ok")
        self.assertEqual(result, [("FME", 1), ("BDO", 2)])
        status, result = ligsplit.match_assignments(["NOPE"], molecules)
        self.assertEqual(status, "mismatch")

    def test_sdf_multi_molecule_split_integration(self):
        self.ws.write(
            "inputs/two.sdf",
            "molA\n  OpenBabel\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\n"
            "$$$$\nmolB\n  OpenBabel\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\n"
            "$$$$\n",
        )
        code, data, run_dir = self._run(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n"
            "      - {file: inputs/two.sdf, names: [molA, molB], charge: 0}\n"
        )
        self.assertEqual(code, 0, data)
        steps = data["systems"]["comp"]["steps"]
        self.assertEqual(steps["split_ligand"]["status"], "done")
        self.assertEqual(steps["ligand_prep"]["status"], "done")
        lig_dir = os.path.join(run_dir, "comp", "05_ligand_prep")
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "molA_GMX.itp")))
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "molB_GMX.itp")))

    def test_ambiguous_choice_flow(self):
        blocks = MULTI_MOL2.split("@<TRIPOS>MOLECULE")
        fme = "@<TRIPOS>MOLECULE" + blocks[1]
        bdo = "@<TRIPOS>MOLECULE" + blocks[2]
        self.ws.write("inputs/two.mol2", fme + fme + bdo)
        systems_block = (
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n"
            "      - {file: inputs/two.mol2, names: [FME, BDO], charge: 0}\n"
        )
        code, data, run_dir = self._run(systems_block)
        self.assertEqual(code, 0)  # 暂停等待选择，run 正常结束
        st = data["systems"]["comp"]["steps"]["split_ligand"]
        self.assertEqual(st["status"], "awaiting_input")
        self.assertIn("FME", st["choice"]["question"])
        self.assertEqual(
            [c["key"] for c in st["choice"]["candidates"]], ["1", "2"]
        )
        # 无效选择被拒绝
        with self.assertRaises(ConfigError):
            cmd_retry(run_dir, "comp", "split_ligand", select="9")
        cmd_retry(run_dir, "comp", "split_ligand", select="2")
        code2, data2, _ = self._run(systems_block)
        self.assertEqual(code2, 0, data2)
        st2 = data2["systems"]["comp"]["steps"]["split_ligand"]
        self.assertEqual(st2["status"], "done")
        self.assertNotIn("choice", st2)
        lig_dir = os.path.join(run_dir, "comp", "05_ligand_prep")
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "FME_GMX.itp")))
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "BDO_GMX.itp")))

    def test_pymol_split_ligand_integration(self):
        blocks = MULTI_MOL2.split("@<TRIPOS>MOLECULE")
        self.ws.write(
            "inputs/two.mol2",
            "@<TRIPOS>MOLECULE"
            + blocks[1]
            + "@<TRIPOS>MOLECULE"
            + blocks[2],
        )
        code, data, run_dir = self._run(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n"
            "      - {file: inputs/two.mol2, names: [FME, BDO], charge: 0}\n",
            lig_step="pymol_split_ligand",
        )
        self.assertEqual(code, 0, data)
        steps = data["systems"]["comp"]["steps"]
        self.assertEqual(steps["pymol_split_ligand"]["status"], "done")
        lig_dir = os.path.join(run_dir, "comp", "05_ligand_prep")
        self.assertTrue(os.path.isfile(os.path.join(lig_dir, "FME_GMX.itp")))

    def test_complex_merge_same_name_counts_and_single_include(self):
        merged_pdb = (
            TINY_PDB.replace("END\n", "")
            + "HETATM 1933  C   UNK A 501       2.661  -7.976   3.560  1.00  0.00           C\n"
            + "HETATM 1934  C   UNK A 502       3.386  -5.586   4.460  1.00  0.00           C\n"
            + "END\n"
        )
        self.ws.write("inputs/merged.pdb", merged_pdb)
        code, data, run_dir = self._run(
            "  - name: comp\n"
            "    complex:\n"
            "      file: inputs/merged.pdb\n"
            "      ligands:\n"
            "        - {name: UNK, charge: 0}\n"
            "        - {name: UNK, charge: 0}\n"
        )
        self.assertEqual(code, 0, data)
        top = os.path.join(
            run_dir, "comp", "06_complex_merge", "comp_complex.top"
        )
        with open(top, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("UNK          2", content)
        self.assertEqual(content.count("UNK_501_GMX.itp"), 1)
        self.assertNotIn("UNK_502_GMX.itp", content)
        gro_path = os.path.join(
            run_dir, "comp", "06_complex_merge", "comp_complex.gro"
        )
        atoms = gro.read_gro(gro_path)["natoms"]
        self.assertEqual(atoms, 10)  # 8 蛋白 + UNK_501(1) + UNK_502(1)

    def test_multi_model_pdb_rejected(self):
        pdb = (
            "MODEL        1\n"
            "HETATM    1  C   LIG     1       1.000   1.000   1.000  1.00  0.00           C\n"
            "ENDMDL\n"
            "MODEL        2\n"
            "HETATM    1  C   LIG     1       2.000   2.000   2.000  1.00  0.00           C\n"
            "ENDMDL\n"
            "END\n"
        )
        self.ws.write("inputs/lig.pdb", pdb)
        code, data, _ = self._run(
            "  - name: comp\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands:\n      - {name: LIG, file: inputs/lig.pdb, charge: 0}\n"
        )
        self.assertEqual(code, 2)
        st = data["systems"]["comp"]["steps"]["split_ligand"]
        self.assertEqual(st["status"], "awaiting_input")
        self.assertIn("MODEL", st["note"])

    def test_pure_protein_system_rejected_by_split_step(self):
        code, data, _ = self._run(
            "  - name: prot\n    protein:\n      file: inputs/protein_A.pdb\n"
            "    ligands: []\n"
        )
        self.assertEqual(code, 2)
        st = data["systems"]["prot"]["steps"]["split_complex"]
        self.assertEqual(st["status"], "awaiting_input")
        self.assertIn("纯蛋白体系", st["note"])

    def test_acpype_mismatch_error_lists_actual_files(self):
        merged_pdb = (
            TINY_PDB.replace("END\n", "")
            + "HETATM 1933  C   UNK A 501       2.661  -7.976   3.560  1.00  0.00           C\n"
            + "END\n"
        )
        self.ws.write("inputs/merged.pdb", merged_pdb)
        systems_block = (
            "  - name: comp\n"
            "    complex:\n"
            "      file: inputs/merged.pdb\n"
            "      ligands:\n"
            "        - {name: UNK, charge: 0}\n"
        )
        env = dict(self.env)
        env["FAKE_ACPYPE_STEM"] = "WRONG"
        with patch.dict(os.environ, env):
            systems_path = self.ws.systems_yaml(systems_block)
            workflow = load_workflow(self._workflow())
            cfg = load_systems(systems_path)
            work_dir = cfg.resolve_work_dir()
            code = Runner(workflow, cfg, work_dir, log=None).run()
            data = RunState(work_dir).load()
        self.assertEqual(code, 2)
        st = data["systems"]["comp"]["steps"]["ligand_prep"]
        self.assertEqual(st["status"], "failed")
        self.assertIn("WRONG_GMX.itp", st["error"])


class PrecheckTests(unittest.TestCase):
    def test_analysis_resolve_inputs_chooses_trajectory(self):
        from mdkit.registry import FileRegistry

        system = NS(name="s")
        registry = FileRegistry("/tmp/r", system)
        registry.set("corrected_xtc", "/tmp/c.xtc")
        logicals = RmsdStep().resolve_inputs(system, registry)
        self.assertIn("corrected_xtc", logicals)
        self.assertNotIn("md_xtc", logicals)
        registry2 = FileRegistry("/tmp/r", system)
        registry2.set("md_xtc", "/tmp/m.xtc")
        logicals2 = RmsdStep().resolve_inputs(system, registry2)
        self.assertIn("md_xtc", logicals2)
        self.assertNotIn("corrected_xtc", logicals2)

    def test_mdp_signature_changes_with_template(self):
        d = tempfile.mkdtemp(prefix="mdkit_mdpsig_")
        mdp = os.path.join(d, "custom.mdp")
        with open(mdp, "w", encoding="utf-8") as fh:
            fh.write("nsteps = 100\n")
        step = EmStep()
        s1 = step.mdp_signature({"mdp": "custom"}, d)
        with open(mdp, "w", encoding="utf-8") as fh:
            fh.write("nsteps = 200\n")
        s2 = step.mdp_signature({"mdp": "custom"}, d)
        self.assertNotEqual(s1["template_sha256"], s2["template_sha256"])

    def test_bench_tick_skips_none_time(self):
        from mdkit.bench import Sampler

        wf_path = os.path.join(tempfile.mkdtemp(prefix="mdkit_bench_"), "w.yaml")
        with open(wf_path, "w", encoding="utf-8") as fh:
            fh.write(
                "name: w\nsteps:\n  - step: md\n  - step: em\n  - step: nvt\n"
            )
        wf = load_workflow(wf_path)
        sampler = Sampler(wf, gpu_ok=False)
        with patch(
            "mdkit.bench.step_progress",
            return_value={"step": None, "time_ps": None, "nsteps": None, "percent": None},
        ):
            sampler.tick({"sys": {"rundir": "/tmp/x", "slot": 0}})  # 不抛异常

    def test_ctl_init_rejects_bad_concurrency(self):
        from mdkit import ctl as ctl_mod

        ws = TempWorkspace()
        wf = ws.write(
            "workflow.yaml",
            "name: w\nsteps:\n  - step: env_check\n  - step: em\n",
        )
        sysf = ws.systems_yaml(
            "  - name: s\n    protein:\n      file: inputs/p.pdb\n"
        )
        args = NS(
            workflow=wf,
            systems=sysf,
            work_dir_base=os.path.join(ws.root, "batch"),
            slot=[],
            concurrency=0,
            system=None,
            json=False,
        )
        with self.assertRaises(ConfigError):
            ctl_mod.ctl_init(args, None)
        ws.cleanup()


if __name__ == "__main__":
    unittest.main()
