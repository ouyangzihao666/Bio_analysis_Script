"""bench suite parsing, slot helpers and integration with fake tools."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import unittest
from unittest.mock import patch

from mdkit.batch import SlotScheduler, parse_slots, slot_gpu
from mdkit.bench import load_suite, run_bench
from mdkit.cliargs import merge_cli_options
from mdkit.config import load_systems
from mdkit.exceptions import ConfigError

from tests.helpers import MULTI_MOL2, TempWorkspace, make_fake_ligand_tools


class BenchUnitTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    def test_suite_load(self):
        path = self.ws.write(
            "bench.yaml",
            "tests:\n"
            "  - name: t1\n"
            "    slots: [\"-gpu_id 0\", \"-gpu_id 1\"]\n"
            "    systems: [a, b]\n",
        )
        suite = load_suite(path)
        self.assertEqual(suite["tests"][0]["name"], "t1")
        self.assertEqual(len(suite["tests"][0]["slots"]), 2)

    def test_suite_requires_slots(self):
        path = self.ws.write("bench.yaml", "tests:\n  - name: x\n    slots: []\n")
        with self.assertRaises(ValueError):
            load_suite(path)

    def test_suite_rejects_gpu_ntomp_without_ntmpi(self):
        path = self.ws.write(
            "bench.yaml",
            "tests:\n"
            "  - name: t1\n"
            '    slots: ["-ntomp 32 -gpu_id 1 -pinoffset 64"]\n',
        )
        with self.assertRaises(ConfigError):
            load_suite(path)

    def test_suite_accepts_ntmpi_slots(self):
        path = self.ws.write(
            "bench.yaml",
            "tests:\n"
            "  - name: t1\n"
            '    slots: ["-ntmpi 1 -ntomp 32 -gpu_id 1 -pinoffset 64"]\n',
        )
        suite = load_suite(path)
        self.assertEqual(len(suite["tests"][0]["slots"]), 1)

    def test_slot_gpu(self):
        self.assertEqual(slot_gpu({"args": "-ntomp 16 -gpu_id 1 -pinoffset 64"}), "1")
        self.assertIsNone(slot_gpu({"args": "-ntomp 16"}))

    def test_slot_merge_last_wins(self):
        merged = merge_cli_options(
            shlex.split("-pin on -ntmpi 1 -ntomp 16"),
            shlex.split("-ntomp 32 -gpu_id 0 -pinoffset 0"),
        )
        self.assertEqual(merged.count("-ntomp"), 1)
        self.assertEqual(merged[merged.index("-ntomp") + 1], "32")

    def test_temp_systems_keeps_source_mol_index(self):
        # 多分子 mol2 按 names 展开后，每个配体依赖 _source_mol_index
        # 定位自己的分子段；临时 systems.yaml 必须透传，否则子进程会
        # 把整个 mol2 交给 obabel，只取到第一个分子（结构错配）。
        mol2_path = self.ws.write("inputs/two_ligs.mol2", MULTI_MOL2)
        systems_path = self.ws.write(
            "systems.yaml",
            "work_dir: ./result\nsystems:\n"
            "  - name: sysX\n"
            "    protein: {file: inputs/protein_A.pdb}\n"
            "    ligands:\n"
            "      - {name: FME, file: %s, charge: 0, names: [FME, BDO]}\n"
            % mol2_path,
        )
        self.ws.add_protein("protein_A.pdb")
        self.ws.write(
            "workflow.yaml",
            "name: t\nsteps:\n  - step: md\n",
        )
        scheduler = SlotScheduler(
            os.path.join(self.ws.root, "workflow.yaml"),
            systems_path,
            os.path.join(self.ws.root, "out"),
        )
        systems_cfg = load_systems(systems_path)
        system = systems_cfg.system_by_name("sysX")
        self.assertEqual(len(system.ligands), 2)
        self.assertEqual(
            [lig.source_mol_index for lig in system.ligands], [0, 1]
        )
        tmp = os.path.join(self.ws.root, "tmp_sys.yaml")
        scheduler._write_temp_systems(system, "", tmp)
        with open(tmp, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("_source_mol_index: 0", text)
        self.assertIn("_source_mol_index: 1", text)
        # 重新加载临时文件后，索引应仍然存在
        reloaded = load_systems(tmp).system_by_name("sysX")
        self.assertEqual(
            [lig.source_mol_index for lig in reloaded.ligands], [0, 1]
        )


class BenchIntegrationTests(unittest.TestCase):
    """End-to-end bench run with fake gmx / nvidia-smi / ligand tools."""

    def setUp(self):
        self.ws = TempWorkspace()
        self.ws.add_protein("protein_A.pdb")
        self.ws.add_protein("protein_B.pdb")
        self.ws.add_ligand("lig.sdf")
        self.ws.write(
            "inputs/merged.pdb",
            (
                "ATOM      1  N   MET A   1      11.608  14.140  11.080  1.00  0.00           N\n"
                "ATOM      2  CA  MET A   1      12.122  13.469  12.258  1.00  0.00           C\n"
                "END\n"
            ),
        )
        self.ws.write(
            "systems.yaml",
            "systems:\n"
            "  - name: sysA\n"
            "    protein: {file: inputs/protein_A.pdb}\n"
            "    ligands:\n"
            "      - {name: FDME, file: inputs/lig.sdf, charge: 0}\n"
            "  - name: sysB\n"
            "    protein: {file: inputs/protein_B.pdb}\n"
            "    ligands: []\n",
        )
        self.ws.write(
            "workflow.yaml",
            "name: bench\nsteps:\n"
            "  - step: env_check\n"
            "  - step: protein_prep\n"
            "  - step: ligand_prep\n"
            "  - step: complex_merge\n"
            "  - step: box\n"
            "  - step: solvate\n"
            "  - step: ions\n"
            "  - step: em\n"
            "  - step: nvt\n"
            "  - step: npt\n"
            "  - step: md\n"
            "    params:\n"
            "      mdp_overrides: {nsteps: 200000}\n"
            "  - step: index\n"
            "  - step: traj_correct\n",
        )
        self.ws.write(
            "bench.yaml",
            "tests:\n"
            "  - name: t1\n"
            "    slots: [\"-gpu_id 0\", \"-gpu_id 1\"]\n"
            "    systems: [sysA, sysB]\n",
        )

    def _stale_test_dir(self):
        stale = os.path.join(self.ws.root, "out", "t1")
        os.makedirs(stale, exist_ok=True)
        with open(os.path.join(stale, "run_status.json"), "w") as fh:
            fh.write("{}")
        return stale

    def test_bench_refuses_stale_test_dir(self):
        self._stale_test_dir()
        with self.assertRaises(ConfigError):
            run_bench(
                os.path.join(self.ws.root, "workflow.yaml"),
                os.path.join(self.ws.root, "systems.yaml"),
                os.path.join(self.ws.root, "out"),
                os.path.join(self.ws.root, "bench.yaml"),
                system_filter=["__none__"],
            )

    def test_bench_fresh_removes_stale_dir(self):
        stale = self._stale_test_dir()
        code = run_bench(
            os.path.join(self.ws.root, "workflow.yaml"),
            os.path.join(self.ws.root, "systems.yaml"),
            os.path.join(self.ws.root, "out"),
            os.path.join(self.ws.root, "bench.yaml"),
            system_filter=["__none__"],
            fresh=True,
        )
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(stale))

    def _fake_bin(self, root):
        """gmx + nvidia-smi + ligand tools in one PATH dir."""
        bin_dir = os.path.join(root, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        with open(os.path.join(bin_dir, "gmx"), "w") as fh:
            fh.write(_FAKE_GMX_BENCH)
        with open(os.path.join(bin_dir, "nvidia-smi"), "w") as fh:
            fh.write(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "if sys.argv[1:] == ['-L']:\n"
                "    print('GPU 0: Fake')\n    print('GPU 1: Fake')\n"
                "else:\n"
                "    print('0, 55, 1000')\n    print('1, 77, 2000')\n"
            )
        os.chmod(os.path.join(bin_dir, "gmx"), 0o755)
        os.chmod(os.path.join(bin_dir, "nvidia-smi"), 0o755)
        lig_bin = make_fake_ligand_tools()
        return bin_dir, lig_bin

    def test_bench_runs_and_samplers(self):
        import sys

        bin_dir, lig_bin = self._fake_bin(self.ws.root)
        env = dict(os.environ)
        env["PATH"] = (
            bin_dir
            + os.pathsep
            + lig_bin
            + os.pathsep
            + env.get("PATH", "")
        )
        base = os.path.join(self.ws.root, "bench_out")
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        from mdkit.bench import run_bench

        with patch.dict(os.environ, env):
            code = run_bench(
                os.path.join(self.ws.root, "workflow.yaml"),
                os.path.join(self.ws.root, "systems.yaml"),
                base,
                os.path.join(self.ws.root, "bench.yaml"),
                log=None,
            )
        self.assertEqual(code, 0)
        bench_file = os.path.join(base, "t1", "benchmark.json")
        self.assertTrue(os.path.isfile(bench_file))
        with open(bench_file, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(set(data["per_system"].keys()), {"sysA", "sysB"})
        for name, rec in data["per_system"].items():
            self.assertEqual(rec["samples"], 3)
        self.assertIn("0", data["per_gpu"])
        self.assertIn("1", data["per_gpu"])
        self.assertEqual(data["per_gpu"]["0"]["avg_util_pct"], 55.0)
        self.assertEqual(data["per_gpu"]["1"]["avg_util_pct"], 77.0)
        # 槽位参数应注入所有 mdrun 步骤（em/nvt/npt/md），而不只是 md。
        import yaml

        with open(os.path.join(base, "t1", ".batch", "sysA.yaml"), encoding="utf-8") as fh:
            tmp = yaml.safe_load(fh)
        ov = tmp["systems"][0]["overrides"]
        for step in ("em", "nvt", "npt", "md"):
            self.assertEqual(ov[step]["extra_args"], "-gpu_id 0")


_FAKE_GMX_BENCH = r'''#!/usr/bin/env python3
import os, sys, time

def touch(p):
    if p:
        open(p, "w").close()

def gro_lines(natoms, resname):
    lines = ["fake gmx", str(natoms)]
    for i in range(natoms):
        no = i + 1
        lines.append("%5d%-5s%5s%5d%8.3f%8.3f%8.3f" % (1, resname, ("A%d" % no)[:5], no, 1.0, 1.0, 1.0))
    lines.append("   2.000   2.000   2.000")
    return "\n".join(lines) + "\n"

PROTEIN_GRO = gro_lines(8, "PROT")
SYSTEM_GRO = gro_lines(20, "SOL")
TOP = '#include "forcefield.itp"\n\n[ molecules ]\nProtein 1\n'

def val(key):
    for i, a in enumerate(sys.argv):
        if a == key and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None

args = sys.argv[1:]
if args and args[0] == "--version":
    print("GROMACS version:    2026.2")
    sys.exit(0)
sub = args[0] if args else ""
if sub == "pdb2gmx":
    open(val("-o"), "w").write(PROTEIN_GRO)
    open(val("-p"), "w").write(TOP)
elif sub in ("editconf", "trjconv"):
    src = val("-f") or val("-cp")
    if val("-o") and src and os.path.isfile(src):
        open(val("-o"), "w").write(open(src).read())
    else:
        touch(val("-o"))
elif sub == "solvate":
    src = val("-cp")
    open(val("-o"), "w").write(open(src).read() if src and os.path.isfile(src) else SYSTEM_GRO)
    top = val("-p")
    if top:
        with open(top, "a") as fh:
            fh.write("SOL 1\n")
elif sub == "grompp":
    touch(val("-o"))
elif sub == "genion":
    open(val("-o"), "w").write(SYSTEM_GRO)
    top = val("-p")
    if top:
        with open(top, "a") as fh:
            fh.write("NA 1\n")
elif sub == "mdrun":
    name = val("-deffnm")
    if name:
        with open(name + ".log", "w") as fh:
            fh.write("Step Time\n  1500000 3000.00000\n")
        time.sleep(25)
        for ext in ("gro", "edr", "cpt", "xtc"):
            open(name + "." + ext, "w").write(SYSTEM_GRO if ext == "gro" else "x")
sys.exit(0)
'''


if __name__ == "__main__":
    unittest.main()
