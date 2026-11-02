#!/usr/bin/env python3
"""V2 ledger: the sessions of a record's days that are not in the record. Evidence, not certification.

    python3 left_out.py --sessions-file runs/m1_first13.txt --out runs/m1_left_out.json

A window record covers the sessions someone chose for it, not every session of its days. This lists the rest, one row
each: its id, its chain head, its armed time and its freezes, read from its own log. The heads commit to those logs
whether or not they are ever published. field_record.py --left-out turns the list into the record's scope row.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import chain
import events
import field_record as fr

SCHEMA = "v2-ledger/left-out/0.1"
METHOD = ("every KH-S session folder under the roots dated from the first to the last day of the record's sessions and "
          "not in the record. armed_s is field_record.armed_s (t_s of the last step row minus the first); freezes "
          "counts `freeze` rows; chain_ok is chain.verify_file on the session's own log.")


def entry(folder: Path) -> dict:
    ok, why, rows = chain.verify_file(folder / "session.jsonl")
    head = rows[-1] if rows else {}
    return {"session_id": folder.name, "chain_ok": ok, "chain_problem": None if ok else why,
            "chain_head_seq": head.get("seq"), "chain_head_sha256": head.get("row_sha256"),
            "armed_s": fr.armed_s(SimpleNamespace(rows=rows)),
            "freezes": sum(r.get("kind") == "freeze" for r in rows)}


def manifest(roots: dict, ids: list, chosen_by: str) -> dict:
    days = sorted(sid[4:12] for sid in ids)
    found = {}
    for path in roots.values():
        for d in sorted(Path(path).glob("KH-S*")):
            if days[0] <= d.name[4:12] <= days[-1] and d.name not in ids and (d / "session.jsonl").is_file():
                found[d.name] = entry(d)
    return {"schema": SCHEMA, "chosen_by": chosen_by, "method": METHOD,
            "sessions": [found[k] for k in sorted(found)]}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", default=None, help="name=path (default: events.py's roots)")
    ap.add_argument("--sessions-file", required=True)
    ap.add_argument("--chosen-by", default="the operator, 2026-10-13: the sessions listed in runs/m1_first13.txt")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    roots = {k: events.resolve(v) for k, v in events.parse_roots(a.root or list(events.DEFAULT_ROOTS)).items()}
    m = manifest(roots, events.read_ids(a.sessions_file), a.chosen_by)
    Path(a.out).write_text(json.dumps(m, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    left = m["sessions"]
    print(f"{len(left)} sessions left out: {sum(s['armed_s'] for s in left) / 3600:.3f} armed h, "
          f"{sum(s['freezes'] for s in left)} freezes, {sum(s['chain_ok'] for s in left)} chains verify -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
