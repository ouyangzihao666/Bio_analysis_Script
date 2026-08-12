"""Minimal SDF parsing: record splitting and molecule-name extraction.

mdkit only needs to know how many molecules a ligand file contains and what
their titles are, so this parser deliberately stays tiny: records are split
on ``$$$$`` and the first non-empty line of each record is the title.
"""

from __future__ import annotations

import os
from typing import Dict, List

from mdkit.exceptions import ConfigError


RECORD_END = "$$$$"


def parse_molecules(path: str) -> List[Dict]:
    """Split an SDF file into molecule records.

    Each block dict: {name, natoms_hint, lines, index}.
    ``name`` is the record title (empty titles fall back to ``mol_<n>``).
    """
    if not os.path.isfile(path):
        raise ConfigError("sdf 文件不存在: %s" % path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw_lines = fh.read().splitlines()
    blocks: List[Dict] = []
    current: List[str] = []
    for line in raw_lines:
        current.append(line)
        if line.rstrip() == RECORD_END:
            blocks.append(current)
            current = []
    if current:
        # Tolerate a missing trailing $$$$ by treating the remainder as a record.
        blocks.append(current)
    out = []
    for idx, lines in enumerate(blocks):
        # The SDF title is the first line of the record; an empty title is
        # not a valid name, so fall back to mol_<n>.
        title = lines[0].strip() if lines else ""
        out.append(
            {
                "name": title or "mol_%d" % (idx + 1),
                "lines": lines,
                "index": idx,
            }
        )
    return out


def write_molecule(path: str, block: Dict) -> None:
    """Write one SDF record back to a single-molecule SDF file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(block["lines"]) + "\n")
