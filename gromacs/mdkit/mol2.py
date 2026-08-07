"""Tripos MOL2 parsing and multi-molecule splitting."""

from __future__ import annotations

import os
import re
from typing import Dict, List

from mdkit.exceptions import ConfigError


MOLECULE_HEADER = "@<TRIPOS>MOLECULE"


def parse_molecules(path: str) -> List[Dict]:
    """Split a mol2 file into its @<TRIPOS>MOLECULE blocks.

    Each block dict: {name, natoms, substructures, lines, index}.
    """
    if not os.path.isfile(path):
        raise ConfigError("mol2 文件不存在: %s" % path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw_lines = fh.read().splitlines()
    blocks = []
    current = None
    for line in raw_lines:
        if line.strip() == MOLECULE_HEADER:
            if current is not None:
                blocks.append(current)
            current = {
                "name": None,
                "natoms": 0,
                "substructures": [],
                "lines": [line],
                "index": len(blocks),
            }
        elif current is not None:
            current["lines"].append(line)
    if current is not None:
        blocks.append(current)
    for block in blocks:
        _fill_block(block, path)
    return blocks


def _fill_block(block: Dict, path: str) -> None:
    name = None
    counts = None
    in_atom = False
    for line in block["lines"][1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if name is None:
            name = stripped
            continue
        if counts is None:
            counts = stripped
            continue
        if stripped.startswith("@<TRIPOS>"):
            in_atom = stripped == "@<TRIPOS>ATOM"
            continue
        if in_atom:
            fields = line.split()
            if len(fields) >= 8:
                substructure = fields[7]
                if substructure not in block["substructures"]:
                    block["substructures"].append(substructure)
    block["name"] = name or "mol_%d" % (block["index"] + 1)
    try:
        block["natoms"] = int(counts.split()[0]) if counts else 0
    except (ValueError, IndexError):
        block["natoms"] = 0


def molecule_name(block: Dict) -> str:
    """Prefer the substructure id (e.g. FME0 -> FME), else the molecule name."""
    if block.get("substructures"):
        clean = re.sub(r"\d+$", "", block["substructures"][0]).strip()
        if clean:
            return clean
    return block.get("name") or "mol_%d" % (block.get("index", 0) + 1)


def write_molecule(path: str, block: Dict) -> None:
    """Write one molecule block back to a single-molecule mol2 file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(block["lines"]) + "\n")


def extract_molecule(src_path: str, out_path: str, selector) -> Dict:
    """Write the molecule block matching ``selector`` (name/substructure/index)."""
    blocks = parse_molecules(src_path)
    target = None
    for block in blocks:
        if block["index"] == selector:
            target = block
            break
        if block["name"] == selector or molecule_name(block) == selector:
            target = block
            break
    if target is None:
        raise ConfigError(
            "mol2 中未找到分子段 %r（%s 共 %d 段）" % (selector, src_path, len(blocks))
        )
    write_molecule(out_path, target)
    return target


def count_components_in_block(block: Dict) -> int:
    """Count connected fragments in one mol2 molecule block (via BOND section)."""
    n = 0
    in_atom = False
    for line in block["lines"]:
        stripped = line.strip()
        if stripped.startswith("@<TRIPOS>ATOM"):
            in_atom = True
            continue
        if stripped.startswith("@<TRIPOS>"):
            in_atom = False
            continue
        if in_atom and stripped and not stripped.startswith("#"):
            n += 1
    if n <= 0:
        return 0
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    in_bond = False
    for line in block["lines"]:
        stripped = line.strip()
        if stripped.startswith("@<TRIPOS>BOND"):
            in_bond = True
            continue
        if stripped.startswith("@<TRIPOS>"):
            in_bond = False
            continue
        if in_bond:
            fields = line.split()
            if len(fields) >= 4:
                try:
                    a, b = int(fields[1]) - 1, int(fields[2]) - 1
                except ValueError:
                    continue
                if 0 <= a < n and 0 <= b < n:
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[ra] = rb
    return len({find(i) for i in range(n)})
