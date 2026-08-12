"""Shared ligand-file splitting logic (mol2 / sdf backends).

Both ``split_ligand`` (deterministic parser) and ``pymol_split_ligand``
(PyMOL state extraction) use the same name enumeration and matching rules:

- a file with a single molecule passes through untouched;
- a multi-molecule file needs ``names`` whose count equals the molecule
  count and whose values are all present in the file's molecule names
  (multiset check); assignment then follows the configured order;
- when a name has more candidate molecules than it is requested for, the
  caller must ask the user to pick one (``ChoiceError``).
"""

from __future__ import annotations

import shutil
from typing import Dict, List, Optional, Tuple

from mdkit import mol2, sdf
from mdkit.exceptions import ConfigError


def parse_molecules(path: str, fmt: str) -> List[Dict]:
    """Return molecule blocks with ``name`` / ``index`` / ``lines`` keys."""
    if fmt == "mol2":
        blocks = mol2.parse_molecules(path)
        for b in blocks:
            # Prefer the substructure id (FME0 -> FME); raw MOLECULE names
            # from tools like PyMOL (obj03/obj04) are meaningless to users.
            b["name"] = mol2.molecule_name(b)
        return blocks
    if fmt == "sdf":
        return sdf.parse_molecules(path)
    if fmt == "pdb":
        return [{"name": "mol", "lines": [], "index": 0}]
    raise ConfigError("不支持的配体拆分格式: %s" % fmt)


def molecule_names(molecules: List[Dict]) -> List[str]:
    return [m.get("name", "mol_%d" % (m["index"] + 1)) for m in molecules]


def extract_molecule(src: str, out: str, fmt: str, index: int) -> None:
    """Write the ``index``-th molecule (0-based) of ``src`` to ``out``."""
    if fmt == "mol2":
        block = mol2.parse_molecules(src)[index]
        mol2.write_molecule(out, block)
    elif fmt == "sdf":
        block = sdf.parse_molecules(src)[index]
        sdf.write_molecule(out, block)
    elif fmt == "pdb":
        shutil.copyfile(src, out)
    else:
        raise ConfigError("不支持的配体拆分格式: %s" % fmt)


def match_assignments(
    names: List[str],
    molecules: List[Dict],
    pin: Optional[Tuple[str, int]] = None,
) -> Tuple[str, object]:
    """Match configured ``names`` against file ``molecules``.

    ``names`` is a subset of the file's molecules (multiset-wise): every
    requested name is assigned one molecule, and leftover molecules in the
    file are ignored. Returns one of:
      ("ok", [(name, molecule_index), ...])
      ("mismatch", message)
      ("ambiguous", (name, candidates))
    ``pin`` is an optional (name, molecule_index) choice the user already
    made via ``ctl retry --select``.
    """
    supply = {}
    for m in molecules:
        supply[m["name"]] = supply.get(m["name"], 0) + 1
    demand = {}
    for n in names:
        demand[n] = demand.get(n, 0) + 1
    for n in demand:
        if supply.get(n, 0) < demand[n]:
            return (
                "mismatch",
                "配置的配体名 %s 在文件中匹配不到足够的分子（需要 %d 个，"
                "文件中有 %d 个）" % (n, demand[n], supply.get(n, 0)),
            )
    if pin is not None:
        pin_name, pin_index = pin
        if pin_index < 0 or pin_index >= len(molecules):
            return "mismatch", "选择无效：分子序号超出范围"
        if molecules[pin_index]["name"] != pin_name:
            return "mismatch", "选择无效：%s 不在 %s 的候选中" % (pin_index + 1, pin_name)
    # Ambiguity: a name is requested fewer times than it appears in the file.
    for n in names:
        if pin is not None and n == pin[0]:
            continue
        if supply[n] > demand[n]:
            candidates = [
                {
                    "key": str(i + 1),
                    "label": "%s（文件中第 %d 个分子）" % (n, i + 1),
                }
                for i, m in enumerate(molecules)
                if m["name"] == n
            ]
            return "ambiguous", (n, candidates)
    used = set()
    if pin is not None:
        used.add(pin[1])
    assignments = []
    for nm in names:
        if pin is not None and nm == pin[0]:
            assignments.append((nm, pin[1]))
            continue
        for j, m in enumerate(molecules):
            if m["name"] == nm and j not in used:
                used.add(j)
                assignments.append((nm, j))
                break
        else:
            return (
                "mismatch",
                "无法为 %s 分配文件中的分子（剩余候选不足）" % nm,
            )
    return "ok", assignments


def names_for_message(molecules: List[Dict]) -> str:
    return "、".join(
        "%s（第 %d 个分子）" % (m["name"], i + 1)
        for i, m in enumerate(molecules)
    )
