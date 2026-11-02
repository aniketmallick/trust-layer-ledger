"""V1's hash-chain rule, re-implemented here (not imported), so the ledger verifies V1's session logs and its own
label log with the same three checks as experiments/knife_handover/verify_chain.py and anchor/g3.py's verify_chain:

  row n has "seq" n, counting from 1;
  its "prev_sha256" is the previous row's "row_sha256" (null for the first row);
  its "row_sha256" is the sha256 of the row without that field, as JSON with sorted keys and no spaces.

Rows are written as V1 writes them: one JSON object per line, sorted keys. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def row_hash(row: dict) -> str:
    body = {k: v for k, v in row.items() if k != "row_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def read_rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def verify_rows(rows: list[dict]) -> tuple[bool, str]:
    """(ok, reason) for an in-memory chain."""
    prev = None
    for i, row in enumerate(rows):
        if row.get("seq") != i + 1:
            return False, f"row {i + 1}: sequence {row.get('seq')} (a row was deleted or reordered)"
        if row.get("prev_sha256") != prev:
            return False, f"row {i + 1}: its link to the previous row does not match (deleted, edited or reordered)"
        if row_hash(row) != row.get("row_sha256"):
            return False, f"row {i + 1}: its content does not match its hash (edited)"
        prev = row["row_sha256"]
    return True, "ok"


def verify_file(path: Path) -> tuple[bool, str, list[dict]]:
    """(ok, reason, rows). A missing file is an empty chain."""
    p = Path(path)
    if not p.is_file():
        return True, "empty", []
    try:
        rows = read_rows(p)
    except json.JSONDecodeError as e:
        return False, f"not JSON: {e}", []
    ok, why = verify_rows(rows)
    return ok, why, rows


def append_row(path: Path, row: dict) -> dict:
    """Append one row after verifying the whole file; seq, prev_sha256 and row_sha256 are set here (any the caller
    passed are ignored). A broken file is never appended to. Returns the row as written."""
    p = Path(path)
    ok, why, rows = verify_file(p)
    if not ok:
        raise ValueError(f"{p}: the chain is broken ({why}); nothing is appended")
    body = {k: v for k, v in row.items() if k not in ("seq", "prev_sha256", "row_sha256")}
    body["seq"] = len(rows) + 1
    body["prev_sha256"] = rows[-1]["row_sha256"] if rows else None
    body["row_sha256"] = row_hash(body)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(body, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return body
