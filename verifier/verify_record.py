#!/usr/bin/env python3
"""V2 ledger, M4: verify a field record end to end, as from a fresh clone. Evidence, not certification.

    python3 verify_record.py records/KH-S20261005T015041/field_record.json        # exit 0 verified, 1 not

Checks, in order: the record's own rows as a hash chain (V1's rule, re-implemented in chain.py, not imported) and its
head; each source session's chain, verified, and its head against the record; the labels chain, verified, and its row
at the recorded head; every row rebuilt from the sources and the labels up to that head (events re-extracted, labels
re-bound, every number recomputed) and compared with the record; each M2 window's anchor row and label row. A source
that is not on disk fails the check unless --structure-only (then only the record's own chain is checked).

--frames-withheld is for a copy of the sessions published without its frames: everything above is still checked, and
a clip whose .mjpeg alone is missing is counted instead of failing. The result line says how many.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import chain
import events
import field_record as fr
import label

CHAIN_FIELDS = ("seq", "prev_sha256", "row_sha256")


def body(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in CHAIN_FIELDS}


def sources(rec: dict, roots: dict, problems: list, say) -> list | None:
    rows = [r for r in rec["rows"] if r["kind"] == "source"]
    found, missing = [], []
    for r in rows:
        folder = Path(roots.get(r["root"], "/nonexistent")) / r["session_id"]
        if not (folder / "session.jsonl").is_file():
            missing.append(r["session_id"])
            continue
        try:
            s = events.Session(folder, r["root"])
        except events.ChainBroken as e:
            problems.append(f"source {r['session_id']}: its chain does not verify ({e})")
            continue
        if (s.head["seq"], s.head["row_sha256"]) != (r["chain_head_seq"], r["chain_head_sha256"]):
            problems.append(f"source {r['session_id']}: its head is not the one the record names")
        found.append(s)
    if missing:
        problems.append(f"sources not checked: {len(missing)} of {len(rows)} not on disk ({', '.join(missing)}); "
                        "--structure-only checks the record's own chain alone")
        return None
    say(f"sources: {len(found)} of {len(rows)} chains verify; heads match" if len(found) == len(rows) and not problems
        else "sources: problems below")
    return found


def withhold(sessions: list) -> int:
    """A clip whose .mjpeg alone is missing keeps its index row and its .json, both checked, and its sha256 stays bound
    in the chain, so it is compared as verified and counted; only its bytes were not re-hashed. Any other fault in a
    clip (an altered .mjpeg, a missing .json) still fails the rebuild."""
    n = 0
    for s in sessions:
        for c in s.clips.values():
            if c["problem"] == f"{Path(c['mjpeg']).name} is missing":
                c.update(verified=True, problem=None)
                n += 1
    return n


def labels_prefix(rec: dict, path: Path, problems: list, say) -> list | None:
    lab = next(r for r in rec["rows"] if r["kind"] == "labels")
    ok, why, rows = chain.verify_file(path)
    if not ok:
        problems.append(f"labels: {path} does not verify ({why})")
        return None
    n = lab["head_seq"]
    if n and (len(rows) < n or rows[n - 1]["row_sha256"] != lab["head_sha256"]):
        problems.append(f"labels: row {n} of {path} is not the head the record names")
        return None
    say(f"labels: chain ok; row {n} matches the recorded head ({len(rows) - n} later rows not used)")
    return rows[:n]


def windows_bind(win_rows: list, sessions: list, prefix: list, problems: list) -> None:
    by_sid = {s.sid: {r["seq"]: r for r in s.rows} for s in sessions}
    by_seq = {r["seq"]: r for r in prefix}
    for w in win_rows:
        a = by_sid.get(w["session_id"], {}).get(w["anchor_row_seq"])
        if a is None or a["row_sha256"] != w["anchor_row_sha256"]:
            problems.append(f"window {w['window_id']}: its anchor row is not in the source chain")
        if w["label_row_seq"] is not None:
            lr = by_seq.get(w["label_row_seq"])
            if lr is None or lr["row_sha256"] != w["label_row_sha256"] or lr.get("event_id") != w["window_id"]:
                problems.append(f"window {w['window_id']}: its label row does not bind")


def rebuild(rec: dict, sessions: list, prefix: list, problems: list, say) -> None:
    lab = next(r for r in rec["rows"] if r["kind"] == "labels")
    scope = fr.Scope(sessions, [e for s in sessions for e in events.events_of(s)], label.latest_labels(prefix),
                     (lab["head_seq"], lab["head_sha256"]), [], rec["scope"]["window"])
    srcs, ev_rows, _ = fr.parts(scope)
    win_rows = [body(r) for r in rec["rows"] if r["kind"] == "window"]
    windows_bind(win_rows, sessions, prefix, problems)
    want = fr.assemble(rec["scope"]["kind"], scope, srcs, ev_rows, win_rows)
    got = [body(r) for r in rec["rows"]]
    if want == got:
        say(f"rebuilt from the sources: identical ({len(got)} rows)")
        return
    i = next((k for k, (a, b) in enumerate(zip(want, got)) if a != b), min(len(want), len(got)))
    r = got[i] if i < len(got) else want[i]
    what = f"{r.get('kind')} {r.get('name') or r.get('event_id') or ''}".strip()
    problems.append(f"rebuilt from the sources: row {i + 1} ({what}) differs from the record"
                    + (f"; the record has {len(got)} rows, the rebuild {len(want)}" if len(want) != len(got) else ""))


def main(argv: list | None = None, say=print) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("record")
    ap.add_argument("--root", action="append", default=None, help="name=path (default: events.py's roots)")
    ap.add_argument("--labels", default=str(Path(__file__).resolve().parent / "labels" / "labels.jsonl"))
    ap.add_argument("--structure-only", action="store_true")
    ap.add_argument("--frames-withheld", action="store_true", help="a clip whose .mjpeg alone is missing is counted")
    a = ap.parse_args(argv)
    rec = json.loads(Path(a.record).read_text(encoding="utf-8"))
    problems: list = []
    withheld = 0
    if rec.get("schema") != fr.SCHEMA:
        problems.append(f"schema {rec.get('schema')!r} is not {fr.SCHEMA}")
    ok, why = chain.verify_rows(rec.get("rows") or [])
    head_ok = bool(rec.get("rows")) and rec.get("head") == {"seq": rec["rows"][-1]["seq"],
                                                             "row_sha256": rec["rows"][-1]["row_sha256"]}
    if ok and head_ok:
        say(f"record: chain ok ({len(rec['rows'])} rows), head {rec['head']['row_sha256'][:16]}")
    else:
        problems.append(f"record: its chain does not verify ({why if not ok else 'the head is not the last row'})")
    if a.structure_only:
        say("structure only: sources, labels and the rebuild were not checked")
    elif not problems:
        roots = {k: events.resolve(v) for k, v in events.parse_roots(a.root or list(events.DEFAULT_ROOTS)).items()}
        sessions = sources(rec, roots, problems, say)
        prefix = labels_prefix(rec, Path(a.labels), problems, say)
        if sessions is not None and prefix is not None and not problems:
            withheld = withhold(sessions) if a.frames_withheld else 0
            rebuild(rec, sessions, prefix, problems, say)
    for p in problems:
        say(f"  {p}")
    note = (" (structure only)" if a.structure_only else
            f" ({withheld} clip files withheld: their hashes are in the chain, their bytes were not checked)"
            if withheld > 1 else
            " (1 clip file withheld: its hash is in the chain, its bytes were not checked)" if withheld else "")
    say("RESULT: NOT VERIFIED" if problems else "RESULT: VERIFIED" + note)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
