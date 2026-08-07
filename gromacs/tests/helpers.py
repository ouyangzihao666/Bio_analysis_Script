"""Shared test helpers: fake gmx, tiny structures, temp configs."""

from __future__ import annotations

import os
import shutil
import tempfile


FAKE_GMX = r'''#!/usr/bin/env python3
import os, sys

def touch(p):
    if p:
        open(p, "w").close()

GRO = (
    "fake gmx\n"
    "    2\n"
    "    1PROT     N    1   1.000   1.000   1.000\n"
    "    1PROT    CA    2   1.001   1.001   1.001\n"
    "   2.000   2.000   2.000\n"
)
TOP = '#include "forcefield.itp"\n\n[ molecules ]\nProtein 1\n'

args = sys.argv[1:]
if args and args[0] == "--version":
    print("GROMACS version:    2024.1")
    sys.exit(0)
if not args:
    sys.exit(2)
sub = args[0]
fail = os.environ.get("FAKE_GMX_FAIL", "")
if sub in [f.strip() for f in fail.split(",") if f.strip()] and sub != "mdrun":
    sys.stderr.write("fake gmx failure for %s\n" % sub)
    sys.exit(7)

def val(key):
    for i, a in enumerate(args):
        if a == key and i + 1 < len(args):
            return args[i + 1]
    return None

if sub == "pdb2gmx":
    open(val("-o"), "w").write(GRO)
    open(val("-p"), "w").write(TOP)
elif sub in ("editconf", "trjconv", "rms", "rmsf", "gyrate", "hbond", "dssp"):
    touch(val("-o"))
elif sub == "solvate":
    src = val("-cp")
    if src and os.path.isfile(src):
        open(val("-o"), "w").write(open(src).read())
    else:
        touch(val("-o"))
    top = val("-p")
    if top:
        with open(top, "a") as fh:
            fh.write("SOL 1\n")
elif sub == "grompp":
    touch(val("-o"))
elif sub == "genion":
    open(val("-o"), "w").write(GRO)
    top = val("-p")
    if top:
        with open(top, "a") as fh:
            fh.write("NA 1\n")
elif sub == "mdrun":
    name = val("-deffnm")
    if name and name.endswith("_md") and "mdrun" in fail.split(","):
        sys.stderr.write("fake gmx failure for mdrun\n")
        sys.exit(7)
    if name:
        for ext in ("gro", "edr", "log", "cpt", "xtc"):
            open(name + "." + ext, "w").write(GRO if ext == "gro" else "x")
sys.exit(0)
'''


def make_fake_gmx() -> str:
    """Create a fake gmx binary in a temp dir; returns the dir path."""
    d = tempfile.mkdtemp(prefix="mdkit_fakebin_")
    path = os.path.join(d, "gmx")
    with open(path, "w") as fh:
        fh.write(FAKE_GMX)
    os.chmod(path, 0o755)
    return d


def with_fake_path(fake_bin: str, env=None):
    env = dict(env or os.environ)
    env["PATH"] = fake_bin + os.pathsep + env.get("PATH", "")
    return env


TINY_PDB = (
    "ATOM      1  N   MET A   1      11.608  14.140  11.080  1.00  0.00           N\n"
    "ATOM      2  CA  MET A   1      12.122  13.469  12.258  1.00  0.00           C\n"
    "ATOM      3  C   MET A   1      13.455  14.165  12.538  1.00  0.00           C\n"
    "ATOM      4  O   MET A   1      13.544  15.331  12.918  1.00  0.00           O\n"
    "END\n"
)

TINY_GRO = """tiny protein
2
    1PROT     N    1   1.000   1.000   1.000
    1PROT    CA    2   1.001   1.001   1.001
   2.000   2.000   2.000
"""

TINY_LIG_GRO = """tiny ligand
2
    1LIG      C    1   2.100   2.100   2.100
    1LIG      H    2   2.200   2.200   2.200
   2.000   2.000   2.000
"""


def write(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


class TempWorkspace:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="mdkit_test_")
        self.inputs = os.path.join(self.root, "inputs")
        os.makedirs(self.inputs, exist_ok=True)

    def write(self, rel: str, content: str) -> str:
        return write(os.path.join(self.root, rel), content)

    def add_protein(self, name="protein_A.pdb") -> str:
        return self.write("inputs/" + name, TINY_PDB)

    def add_ligand(self, name="ligand_1.sdf") -> str:
        return self.write(
            "inputs/" + name,
            "ligand\n 1\n    1LIG  C    1   0.000   0.000   0.000\n",
        )

    def systems_yaml(self, systems_block: str) -> str:
        return self.write(
            "systems.yaml",
            "work_dir: ./result\nsystems:\n%s\n" % systems_block,
        )

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)
