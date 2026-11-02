#!/usr/bin/env python3
"""Write each record's event.html from the session logs, with no frames in it.

The originals embed the frames. These carry the same rows, lanes, judge calls and labels, and say that the frames are
not published. field_record.html links here, so every link in it still lands on something.

    python3 tools/build_event_pages.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verifier"))
import chain  # noqa: E402
import events  # noqa: E402
import field_record as fr  # noqa: E402
import label  # noqa: E402
import record_pages as pages  # noqa: E402

NOTE = "Frames are not published in this repository. Where a frame was stored, its sha256 is in the logs."
NO_FRAME_JS = "No stored frame for this event."


def media(_ctx, _event) -> dict:
    return {"kind": "none", "frames": [], "note": NOTE}


def build(path: Path) -> int:
    rec = json.loads(path.read_text(encoding="utf-8"))
    policy = "policy" in path.parts
    folder = ROOT / "sources" / ("toceta_live_tote" if policy else "knife_handover")
    sessions = [events.Session(folder / r["session_id"], r["root"]) for r in rec["rows"] if r["kind"] == "source"]
    lab = next(r for r in rec["rows"] if r["kind"] == "labels")
    ok, why, rows = chain.verify_file(ROOT / "labels" / ("policy_labels.jsonl" if policy else "labels.jsonl"))
    if not ok:
        raise SystemExit(f"the labels chain is broken: {why}")
    scope = fr.Scope(sessions, [e for s in sessions for e in events.events_of(s)],
                     label.latest_labels(rows[:lab["head_seq"]]), (lab["head_seq"], lab["head_sha256"]), [],
                     rec["scope"]["window"])
    html = pages.event_html(rec, scope).replace(NO_FRAME_JS, NOTE)
    out = path.parent / "event.html"
    out.write_text(html, encoding="utf-8")
    return len(html)


def main() -> int:
    pages.media = media
    paths = sorted((ROOT / "records").rglob("field_record.json"))
    for p in paths:
        build(p)
    print(f"wrote {len(paths)} event pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
