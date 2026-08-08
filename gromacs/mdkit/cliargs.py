"""Option-level merging for external CLI args (e.g. gmx mdrun).

GROMACS rejects duplicate options, so mdkit assembles a single
deduplicated argv from structured params, user extra args and slot args.
Later occurrences of the same option override earlier ones (last wins).
"""

from __future__ import annotations

import shlex
from typing import List, Optional, Tuple


def parse_options(argv: List[str]) -> List[Tuple[str, Optional[str]]]:
    """Tokenize args into (option, value) pairs, preserving order.

    ``-flag value`` -> ("-flag", "value"); ``-flag`` (no following non-dash
    token) -> ("-flag", None). Positional tokens are kept as flags.
    """
    opts: List[Tuple[str, Optional[str]]] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("-") and len(tok) > 1:
            name = tok
            value = None
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            opts.append((name, value))
        else:
            opts.append((tok, None))
            i += 1
    return opts


def merge_cli_options(*arg_lists: List[str]) -> List[str]:
    """Merge option lists, deduplicating by option name (last wins)."""
    merged: dict = {}
    order: List[str] = []
    for argv in arg_lists:
        for name, value in parse_options(argv):
            if name not in merged:
                order.append(name)
            merged[name] = value
    out: List[str] = []
    for name in order:
        out.append(name)
        if merged[name] is not None:
            out.append(merged[name])
    return out


_GPU_OPTIONS = ("-gpu_id", "-nb", "-pme", "-bonded", "-update", "-dcu", "-gpu_tasks")


def slot_missing_ntmpi(slot_args: str) -> Optional[str]:
    """Explain why gmx 2026 would reject a slot, or None if it is fine.

    When GPUs are used and ``-ntomp`` is set, GROMACS 2026 requires
    ``-ntmpi`` as well; otherwise mdrun fails with a fatal error.
    """
    try:
        tokens = shlex.split(slot_args)
    except ValueError:
        return "槽位参数无法解析: %r" % slot_args
    if "-ntomp" not in tokens or "-ntmpi" in tokens:
        return None
    uses_gpu = False
    for i, tok in enumerate(tokens):
        if tok == "-gpu_id":
            uses_gpu = True
            break
        if tok in _GPU_OPTIONS:
            value = tokens[i + 1] if i + 1 < len(tokens) else ""
            if value and value not in ("cpu", "none"):
                uses_gpu = True
                break
    if not uses_gpu:
        return None
    return (
        "槽位参数同时使用 GPU（%s）与 -ntomp 但缺少 -ntmpi；"
        "GROMACS 2026 要求 GPU 模式下指定线程 MPI 秩数，"
        '请补充 -ntmpi 1，例如 "-ntmpi 1 -ntomp 32 -gpu_id 1 -pinoffset 64"'
        % slot_args
    )
