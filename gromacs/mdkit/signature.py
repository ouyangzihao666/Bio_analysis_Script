"""Signature / provenance helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Optional


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stable_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def params_signature(params: dict) -> str:
    return hashlib.sha256(_stable_json(params).encode("utf-8")).hexdigest()


def inputs_signature(input_hashes: Dict[str, str]) -> str:
    return hashlib.sha256(_stable_json(input_hashes).encode("utf-8")).hexdigest()


def step_signature(
    step_name: str,
    step_version: str,
    params: dict,
    input_hashes: Dict[str, str],
    mdp_info: Optional[dict] = None,
) -> str:
    payload = {
        "step": step_name,
        "version": step_version,
        "params": params,
        "inputs": input_hashes,
        "mdp": mdp_info,
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
