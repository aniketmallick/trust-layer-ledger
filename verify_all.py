#!/usr/bin/env python3
"""Check every record in this repository against the session logs in sources/. Standard library only.

    python3 verify_all.py                 # all 28 records; exit 0 if every one verifies, 1 if not
    python3 verify_all.py window_30d      # records whose path contains this text
    python3 verify_all.py --verbose       # the verifier's full output for each record

Each record goes through verifier/verify_record.py, which checks the record's own hash chain, each source log's chain
and head, the labels chain, and then rebuilds every row from the logs and compares. The frames are not published, so
the verifier runs with --frames-withheld: a clip whose .mjpeg alone is missing is counted, not checked.

The 30-day record has a scope row saying which sessions it left out. That row is rebuilt from runs/m1_left_out.json, so
its totals are checked against that file. The left-out sessions' own logs are not published and are not read.

A policy record also carries a `policy` section and sometimes a `twin` section, kept beside its rows. Those are checked
here for their hash and for their binding to this record. They are not rebuilt: that needs the run config, the model's
train_config.json and the simulator's chain, which are not in this repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "verifier"))
import verify_record  # noqa: E402

WITHHELD = "clip file"


def section_problems(rec: dict, name: str) -> list:
    """The hash of a section kept beside the rows, and its binding to this record."""
    sec = rec.get(name)
    if sec is None:
        return ["the record has no policy section"] if name == "policy" else []
    body = {k: v for k, v in sec.items() if k != "sha256"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    problems = []
    if hashlib.sha256(canon).hexdigest() != sec.get("sha256"):
        problems.append(f"{name} section: its sha256 does not match its content")
    if body.get("binds", {}).get("record_head") != rec.get("head"):
        problems.append(f"{name} section: it is bound to another record head")
    head = body.get("binds", {}).get("session_head")
    src = next((r for r in rec["rows"] if r["kind"] == "source"), {})
    if head is not None and head != src.get("chain_head_sha256"):
        problems.append(f"{name} section: it is bound to another session head")
    return problems


def check(path: Path, root: Path = HERE) -> tuple[bool, list, int]:
    """One record -> (verified, the verifier's lines, clip files not checked)."""
    policy = "policy" in path.parts
    knife, tote = root / "sources" / "knife_handover", root / "sources" / "toceta_live_tote"
    lines: list = []
    rc = verify_record.main([str(path), "--root", f"public={knife}", "--root", f"local={knife}",
                             "--root", f"toceta_live_tote={tote}", "--labels",
                             str(root / "labels" / ("policy_labels.jsonl" if policy else "labels.jsonl")),
                             "--left-out", str(root / "runs" / "m1_left_out.json"), "--frames-withheld"],
                            say=lines.append)
    if policy:
        rec = json.loads(path.read_text(encoding="utf-8"))
        extra = section_problems(rec, "policy") + section_problems(rec, "twin")
        lines += [f"  {p}" for p in extra]
        rc = rc or (1 if extra else 0)
    last = lines[-1 - len(extra)] if policy else lines[-1]
    n = int(last.split("(")[1].split()[0]) if WITHHELD in last else 0
    return rc == 0, lines, n


def main(argv: list | None = None, root: Path = HERE, say=print) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("only", nargs="?", help="check only the records whose path contains this")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    paths = sorted((root / "records").rglob("field_record.json"))
    if a.only:
        paths = [p for p in paths if a.only in str(p)]
    if not paths:
        say("no record found")
        return 1
    ok, withheld, scope = 0, 0, []
    for p in paths:
        good, lines, n = check(p, root)
        ok += good
        withheld += n
        name = str(p.parent.relative_to(root / "records"))
        say(f"{'VERIFIED    ' if good else 'NOT VERIFIED'}  {name}" + (f"  ({n} clip{'s' if n > 1 else ''} withheld)" if n else ""))
        if a.verbose or not good:
            say("\n".join(f"    {x}" for x in lines))
        elif good:
            scope.extend(x for x in lines if x.startswith("scope:"))
    say(f"{ok} of {len(paths)} records verified")
    for x in scope:
        say(x)
    if withheld:
        say(f"{withheld} clip files were not checked: they are not published. Their hashes are in the chains.")
    return 0 if ok == len(paths) else 1


if __name__ == "__main__":
    sys.exit(main())
