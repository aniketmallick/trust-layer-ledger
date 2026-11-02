#!/usr/bin/env python3
"""V2 ledger, M4: the field record. Evidence, not certification.

One record per session, or per window of sessions (default 30 days): field_record.json (rows hash-chained with V1's
rule, each source session's chain head bound in, the labels chain's head bound in) and two pages built from it,
field_record.html and event.html (record_pages.py). Every number is a row with its window, denominator, count, label
coverage and method. Labels count by who made them: one_by_one (the operator at the prompt), bulk (proposals the
operator accepted without looking), model_panel (three model reviewers and an orchestrator). Under rule 4 only the
operator's own labels are ground truth, so a rate is printed only where they cover half the events or more; otherwise
a not_shown row says why.

    python3 field_record.py --session KH-S20261005T015041 --out records/KH-S20261005T015041
    python3 field_record.py --sessions-file runs/m1_first13.txt --window-days 30 --out records/window_30d
    python3 verify_record.py records/KH-S20261005T015041/field_record.json
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import chain
import events
import label

SCHEMA = "v2-ledger/field-record/0.1"
HERE = Path(__file__).resolve().parent
STATEMENT = ("Evidence, not certification. Read from V1's own hash-chained session logs; no claim of compliance or of "
             "safety is made.")
ROBOT = "SO-101 (five arm joints and a gripper); one overhead camera, 640 x 480"
SYSTEM = ("V1 trust layer on a scripted screwdriver handover (trust-layer-demo): hand monitor (monitor 7), monitor "
          "link (8), envelope monitors 1-6, a vision-model judge at checkpoints, a planner, freeze-and-ask")
HUMAN_MIN = 0.5
LABELABLE = ("freeze", "judge_veto", "planner_refusal", "release")
TYPES = LABELABLE + ("operator_hold", "override_stamp")
SOURCES = ("monitor 7 (hand)", "monitor 8 (link)", "monitors 1-6 (envelope)", "code (0)", "judge", "planner",
           "operator")
MENU_STATES = ("ASK", "ASK_RELEASE")
REVIEW = label.REVIEW_KINDS

M_EVENTS = ("events.py (schema {schema}) from the verified V1 chains: one event per source row; nothing is labelled "
            "to be counted")
M_ARMED = ("per session, the control loop's span: t_s of the last step row minus the first (torque on, the arm "
           "holding or moving), summed over the sessions")
M_LABELS = ("the newest label row per event among rows 1..{head} of labels/labels.jsonl, bound to the event's V1 row "
            "(event_row_sha256); human = one_by_one / labelable, any = labelled / labelable")
M_FA = ("false_alarm labels made one by one by the operator, per 100 h of armed time; labels from bulk acceptance or "
        "a model panel are not counted (rule 4); printed only where the operator's labels cover >= 50 % of the events")
M_TP = "true_positive labels made one by one by the operator; printed only where they cover >= 50 % of the events"
M_REL = ("the operator's one-by-one answers to 'Released into an open, ready hand?'; printed only where they cover "
         ">= 50 % of the releases")
M_LAT = (f"{events.METHOD_FRAME_TO_HOLD} Percentiles by nearest rank over the monitor-7 freezes of the window that "
         "carry the value.")
M_MISS = ("the M2 sample (review_misses.py): windows the operator labelled one by one, and the misses among them; "
          "printed only where the operator reviewed >= 50 % of the sampled windows of the scope")
M_PANEL = ("the M2 sample labelled by a model panel (lenient, harsh and evidence-purist reviewers, an orchestrator); "
           "under rule 4 these are not misses or non-misses until the operator labels them")
M_KEYS = "accepted key rows at the freeze and release menus (states ASK, ASK_RELEASE), by key, from the V1 chains"
M_KILLS = "V1 kill rows (torque off), by cause, from the chains"
NOT_COVERED = [
    "servo stop time and stopping distance: the hold is timed to the write of the hold goal, not to the joints at rest",
    "camera exposure, readout and driver buffering before the frame is encoded (frame_t is after the JPEG encode)",
    "anything outside the camera's view, and hands the arm hides from it",
    "misses: the miss review samples windows; a sample labelled only by a model panel shows no operator-reviewed miss",
    "frames between events in sessions recorded before M3: only the freeze photos and judge frames were stored",
    "the judge model's reasoning beyond its stated verdict and reason",
    "deployment: these are development sessions, most under the operator override; no field use is in them",
    "armed time is the control loop's logged span; time with the loop not logging is not in it",
]
REQUIREMENTS = [
    ("EU Machinery Regulation 2023/1230, Annex III 1.2.1", "recording of data on the safety-related decision-making "
     "process (kept one year)",
     "every stop, veto, refusal, hold and release with its inputs, time and the hash of the V1 row it came from; the "
     "label and who made it; each source chain's head",
     "retention for a year (storage is outside this record); the judge model's internal process; continuous recording "
     "between events before M3"),
    ("EU Machinery Regulation 2023/1230, Annex III 1.2.1", "tracing log of interventions and safety-software versions "
     "(kept five years)",
     "operator keys at the menus, kills, override and layer-off sessions; per session the runner's per-file code "
     "hashes, the guard's version and hash, the judge model and rubric hashes, the registration hash",
     "retention for five years; versions between sessions; who changed the code and why (no change log in this "
     "record)"),
    ("EU Machinery Regulation 2023/1230, Annex III 1.1.9", "protection against corruption (tamper evidence)",
     "hash chains: V1's session chains, the labels chain, the clip index, this record's rows; verify_record.py checks "
     "all of them end to end",
     "deletion or replacement of a whole file (a chain shows edits, not a missing session); no signature or external "
     "timestamp; no access control"),
    ("UL 4600", "safety performance indicators: field data collection",
     "counts and rates with their methods: stops by source, false alarms per 100 h where the operator's labels cover "
     ">= 50 %, frame-to-hold latency",
     "indicators tied to a safety case (there is none); thresholds; field data (these are development sessions); "
     "misses reviewed by the operator"),
    ("ISO 10218-1/-2", "protective stop",
     "frame-to-hold time of monitor-7 freezes: the encoded frame to the write of the hold goal",
     "servo stop time and distance, stop category, validation of the stop function to the standard"),
    ("ISO/TS 15066", "collaborative operation: speed and separation, power and force limiting",
     "the planner's standoff refusals and the estimated palm positions (12-73 mm error, V1 STATUS)",
     "contact forces and pressures, separation measured to the standard, body-region limits"),
]


@dataclasses.dataclass
class Scope:
    sessions: list
    events: list
    latest: dict
    labels_head: tuple
    windows: list
    window: dict | None


def find(roots: dict, sid: str) -> tuple:
    for name, path in roots.items():
        if (Path(path) / sid / "session.jsonl").is_file():
            return name, Path(path) / sid
    raise FileNotFoundError(f"{sid}: no session folder under the roots {sorted(roots)}")


def load(roots: dict, ids: list, labels_path: Path, windows_dir: Path | None = None,
         window: dict | None = None) -> Scope:
    sessions = [events.Session(folder, name) for name, folder in (find(roots, sid) for sid in ids)]
    evs = [e for s in sessions for e in events.events_of(s)]
    ok, why, rows = chain.verify_file(Path(labels_path))
    if not ok:
        raise ValueError(f"{labels_path}: the labels chain is broken ({why})")
    latest = label.latest_labels(rows)
    wins = []
    if windows_dir is not None and Path(windows_dir).is_dir():
        wins = [w for w in label.load_events(Path(windows_dir)) if w["session"]["id"] in set(ids)]
    head = (rows[-1]["seq"], rows[-1]["row_sha256"]) if rows else (0, None)
    return Scope(sessions, evs, latest, head, wins, window)


# ------------------------------------------------------------------------------------------ the pieces
def first_step_t(s) -> float | None:
    return next((r["t_s"] for r in s.rows if r["kind"] == "step"), None)


def armed_s(s) -> float:
    steps = [r["t_s"] for r in s.rows if r["kind"] == "step"]
    return round(steps[-1] - steps[0], 3) if len(steps) > 1 else 0.0


def nearest_rank(xs: list, p: float):
    s = sorted(xs)
    if not s:
        return None
    k = max(1, -(-len(s) * p // 100))                                # ceil(n p / 100), at least 1
    return s[int(k) - 1]


def stop_source(e: dict) -> str | None:
    t, sub = e["type"], e["subtype"]
    if t == "freeze":
        mon = sub.split("_")[0]
        return {"m7": SOURCES[0], "m8": SOURCES[1], "m0": SOURCES[3]}.get(mon, SOURCES[2])
    return {"judge_veto": SOURCES[4], "planner_refusal": SOURCES[5], "operator_hold": SOURCES[6]}.get(t)


def week_of(s) -> str:
    d = datetime.date(int(s.sid[4:8]), int(s.sid[8:10]), int(s.sid[10:12]))
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def bound_label(scope: Scope, e: dict) -> dict | None:
    lab = scope.latest.get(e["event_id"])
    return lab if label.bound(lab, e) else None


def source_row(s) -> dict:
    start = next((r for r in s.rows if r["kind"] == "session_start"), {})
    end = next((r for r in reversed(s.rows) if r["kind"] == "session_end"), None)
    runner = (start.get("runner") or {}).get("sha256")
    rubrics = sorted({(r.get("verdict") or {}).get("rubric_sha256") for r in s.rows if r["kind"] == "judge"} - {None})
    clips = [c for c in s.clips.values()]
    return {"kind": "source", "session_id": s.sid, "root": s.root, "chain": f"{s.sid}/session.jsonl",
            "chain_head_seq": s.head["seq"], "chain_head_sha256": s.head["row_sha256"], "rows": len(s.rows),
            "started_iso": start.get("t_iso"),
            "ended_iso": (end or next((r for r in reversed(s.rows) if r.get("t_iso")), {})).get("t_iso"),
            "mode": start.get("mode"), "dry_run": bool(start.get("dry_run")),
            "operator_override": bool(s.meta["operator_override"]), "layer_off": bool(s.meta["layer_off"]),
            "armed_s": armed_s(s), "hand_feed": start.get("hand_feed"),
            "runner_code_sha256": None if not isinstance(runner, dict) else hashlib.sha256(
                json.dumps(runner, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "runner_files": len(runner) if isinstance(runner, dict) else 0, "g3": start.get("g3"),
            "judge_model": (start.get("judge") or {}).get("model"), "rubric_sha256": rubrics,
            "registration_sha256": (start.get("table_map") or {}).get("registration_sha256"),
            "clips": {"attached": len(clips), "verified": sum(c["verified"] for c in clips), "problem": s.clip_problem}}


def event_row(scope: Scope, e: dict, t0: float | None) -> dict:
    lab = bound_label(scope, e)
    st = (e.get("step") or {}).get("t_s")
    lat = (e.get("latency") or {}).get("frame_to_hold_ms") if e["type"] == "freeze" else None
    clip = e.get("clip")
    return {"kind": "event", "event_id": e["event_id"], "type": e["type"], "subtype": e["subtype"],
            "session_id": e["session"]["id"], "source_row_seq": e["source"]["row_seq"],
            "source_row_sha256": e["source"]["row_sha256"],
            "step_t_s": st, "rel_s": None if st is None or t0 is None else round(st - t0, 3),
            "wall_iso": e["time"]["wall_iso"], "label": None if lab is None else lab["label"],
            "review": None if lab is None else (lab.get("review") or "one_by_one"),
            "label_row_seq": None if lab is None else lab["seq"],
            "label_row_sha256": None if lab is None else lab["row_sha256"], "frame_to_hold_ms": lat,
            "photo": (e.get("photo") or {}).get("file"),
            "clip": None if not clip else {k: clip[k] for k in ("mjpeg", "mjpeg_sha256", "verified")}}


def window_row(scope: Scope, w: dict, t0: float | None) -> dict:
    lab = bound_label(scope, w)
    st = (w.get("step") or {}).get("t_s")
    return {"kind": "window", "window_id": w["event_id"], "category": w["window"]["category"],
            "era": w["window"]["era"], "session_id": w["session"]["id"], "anchor_row_seq": w["source"]["row_seq"],
            "anchor_row_sha256": w["source"]["row_sha256"],
            "rel_s": None if st is None or t0 is None else round(st - t0, 3),
            "label": None if lab is None else lab["label"],
            "review": None if lab is None else (lab.get("review") or "one_by_one"),
            "label_row_seq": None if lab is None else lab["seq"],
            "label_row_sha256": None if lab is None else lab["row_sha256"], "sampling": w.get("sampling")}


def metric(name: str, value, unit: str, window: str, method: str, count=None, denominator=None, coverage=None) -> dict:
    return {"kind": "metric", "name": name, "value": value, "unit": unit, "window": window, "denominator": denominator,
            "count": count, "coverage": coverage, "method": method}


def not_shown(name: str, why: str) -> dict:
    return {"kind": "not_shown", "name": name, "why": why}


def label_split(evs: list) -> dict:
    """evs: [(event row)] of one type -> counts by review kind and by label."""
    by_review = Counter(r["review"] for r in evs if r["review"])
    by_label = defaultdict(Counter)
    for r in evs:
        if r["review"]:
            by_label[r["review"]][r["label"]] += 1
    return {"labelable": len(evs), **{k: by_review.get(k, 0) for k in REVIEW},
            "unreviewed": sum(1 for r in evs if not r["review"]),
            "by_label": {k: dict(sorted(v.items())) for k, v in sorted(by_label.items())}}


def pct(x: float) -> str:
    return f"{100 * x:.0f} %"


# ------------------------------------------------------------------------------------------ the rows
def label_rows(ev_rows: list, armed_h: float, window: str, head: int) -> list:
    out = []
    for t in LABELABLE:
        rows = [r for r in ev_rows if r["type"] == t]
        if not rows:
            continue
        sp = label_split(rows)
        human = round(sp["one_by_one"] / sp["labelable"], 4)
        cov = {"human": human, "any": round((sp["labelable"] - sp["unreviewed"]) / sp["labelable"], 4)}
        out.append(metric(f"labels/{t}", sp, "events", window, M_LABELS.format(head=head), count=sp["labelable"],
                          denominator={"what": f"{t} events", "value": sp["labelable"]}, coverage=cov))
        own = Counter(r["label"] for r in rows if r["review"] == "one_by_one")
        why = (f"the operator's own labels cover {pct(human)} of the {sp['labelable']} {t} events (< 50 %): no "
               f"number printed; the bulk and model-panel labels are counted in labels/{t}")
        if t == "release":
            if human >= HUMAN_MIN:
                out.append(metric("release_outcomes", {k: own.get(k, 0) for k in ("yes", "no", "unclear")}, "releases",
                                  window, M_REL, count=sp["one_by_one"],
                                  denominator={"what": "releases", "value": sp["labelable"]}, coverage=cov))
            else:
                out.append(not_shown("release_outcomes", why))
            continue
        if human >= HUMAN_MIN and armed_h > 0:
            fa = own.get("false_alarm", 0)                  # from the denominator as printed: a reader gets it back
            out.append(metric(f"false_alarms_per_100h/{t}", round(100.0 * fa / armed_h, 2), "per 100 h armed", window,
                              M_FA, count=fa, denominator={"what": "armed hours", "value": armed_h},
                              coverage=cov))
            out.append(metric(f"true_positives/{t}", own.get("true_positive", 0), "events", window, M_TP,
                              count=own.get("true_positive", 0), denominator={"what": f"{t} events",
                                                                               "value": sp["labelable"]}, coverage=cov))
        else:
            reason = why if human < HUMAN_MIN else "no armed time in the scope"
            out += [not_shown(f"false_alarms_per_100h/{t}", reason), not_shown(f"true_positives/{t}", reason)]
    return out


def miss_rows(win_rows: list, window: str) -> list:
    if not win_rows:
        return [not_shown("misses_found_in_review", "no M2 window was sampled in this scope")]
    n = len(win_rows)
    own = [r for r in win_rows if r["review"] == "one_by_one"]
    cov = {"human": round(len(own) / n, 4)}
    out = []
    if len(own) / n >= HUMAN_MIN:
        out.append(metric("misses_found_in_review", sum(r["label"] == "miss" for r in own), "windows", window, M_MISS,
                          count=sum(r["label"] == "miss" for r in own),
                          denominator={"what": "windows reviewed by the operator", "value": len(own)}, coverage=cov))
    else:
        out.append(not_shown("misses_found_in_review", f"the operator reviewed {len(own)} of the {n} sampled windows "
                                                       f"({pct(len(own) / n)} < 50 %): no miss count printed"))
    panel = [r for r in win_rows if r["review"] == "model_panel"]
    if panel:
        c = Counter(r["label"] for r in panel)
        out.append(metric("model_panel_window_review", {k: c.get(k, 0) for k in ("miss", "correct_no_fire", "unclear")},
                          "windows", window, M_PANEL, count=len(panel),
                          denominator={"what": "sampled windows", "value": n},
                          coverage={"human": cov["human"], "model_panel": round(len(panel) / n, 4)}))
    return out


def parts(scope: Scope) -> tuple[list, list, list]:
    """The rows read straight from the sources: (source rows, event rows, window rows)."""
    t0 = {s.sid: first_step_t(s) for s in scope.sessions}
    return ([source_row(s) for s in scope.sessions],
            [event_row(scope, e, t0[e["session"]["id"]]) for e in scope.events],
            [window_row(scope, w, t0[w["session"]["id"]]) for w in scope.windows])


def build_rows(scope: Scope, kind: str) -> list:
    return assemble(kind, scope, *parts(scope))


def assemble(kind: str, scope: Scope, srcs: list, ev_rows: list, win_rows: list) -> list:
    """Every row of the record, from its parts; verify_record.py rebuilds the same rows from the sources."""
    armed_h = round(sum(r["armed_s"] for r in srcs) / 3600.0, 6)
    ids = [s.sid for s in scope.sessions]
    window = (f"{scope.window['days']} days to {scope.window['to']} ({len(ids)} sessions)" if scope.window
              else f"session {ids[0]}" if len(ids) == 1 else f"{len(ids)} sessions")
    rows = [{"kind": "header", "statement": STATEMENT, "robot": ROBOT, "system": SYSTEM, "scope": kind,
             "sessions": ids, "window": scope.window, "events_schema": events.SCHEMA}]
    rows += srcs
    rows.append({"kind": "labels", "chain": "labels/labels.jsonl", "head_seq": scope.labels_head[0],
                 "head_sha256": scope.labels_head[1], "method": M_LABELS.format(head=scope.labels_head[0])})
    rows += ev_rows + win_rows
    rows.append(metric("armed_hours", armed_h, "h", window, M_ARMED, count=len(ids)))
    by_type = Counter(r["type"] for r in ev_rows)
    for t in TYPES:
        if by_type.get(t):
            rows.append(metric(f"events_by_type/{t}", by_type[t], "events", window,
                               M_EVENTS.format(schema=events.SCHEMA), count=by_type[t]))
    src = Counter(stop_source(e) for e in scope.events if stop_source(e))
    rows.append(metric("stops_by_source", {k: src.get(k, 0) for k in SOURCES}, "events", window,
                       M_EVENTS.format(schema=events.SCHEMA) + "; the source is the monitor, the judge, the planner or "
                       "the operator that stopped or held the arm", count=sum(src.values())))
    per_week = defaultdict(Counter)
    by_sid = {s.sid: s for s in scope.sessions}
    for e in scope.events:
        if stop_source(e):
            per_week[week_of(by_sid[e["session"]["id"]])][stop_source(e)] += 1
    rows.append(metric("stops_by_source_per_week", {w: {k: c.get(k, 0) for k in SOURCES}
                                                    for w, c in sorted(per_week.items())},
                       "events", window, "as stops_by_source, by the ISO week of the session's start", count=None))
    rows += label_rows(ev_rows, armed_h, window, scope.labels_head[0])
    rows.append(metric("unreviewed_events", {t: sum(1 for r in ev_rows if r["type"] == t and not r["review"])
                                             for t in LABELABLE if by_type.get(t)}, "events", window,
                       M_LABELS.format(head=scope.labels_head[0]),
                       count=sum(1 for r in ev_rows if r["type"] in LABELABLE and not r["review"])))
    rows += miss_rows(win_rows, window)
    lats = [r["frame_to_hold_ms"] for r in ev_rows if r["frame_to_hold_ms"] is not None]
    rows.append(metric("frame_to_hold_ms", {"n": len(lats), "p50": nearest_rank(lats, 50),
                                            "p95": nearest_rank(lats, 95), "max": max(lats) if lats else None},
                       "ms", window, M_LAT, count=len(lats),
                       denominator={"what": "monitor-7 freezes with frame_to_hold_ms", "value": len(lats)}))
    rows.append(metric("frame_to_hold_ms_series", [[r["session_id"], r["rel_s"], r["frame_to_hold_ms"]]
                                                   for r in ev_rows if r["frame_to_hold_ms"] is not None],
                       "ms", window, M_LAT + " Each: session, seconds into its control loop, ms.", count=len(lats)))
    keys, kills = Counter(), Counter()
    for s in scope.sessions:
        for r in s.rows:
            if r["kind"] == "key" and r.get("accepted") and r.get("state") in MENU_STATES:
                keys[str(r.get("key"))] += 1
            if r["kind"] == "kill":
                kills[str(r.get("cause"))] += 1
    rows.append(metric("operator_keys", dict(sorted(keys.items())), "keys", window, M_KEYS, count=sum(keys.values())))
    rows.append(metric("kills", dict(sorted(kills.items())), "kills", window, M_KILLS, count=sum(kills.values())))
    rows.append(metric("override_sessions", sum(r["operator_override"] for r in srcs), "sessions", window,
                       "sessions whose chain carries V1's operator-override stamp", count=len(srcs),
                       denominator={"what": "sessions", "value": len(srcs)}))
    rows.append(metric("layer_off_sessions", sum(r["layer_off"] for r in srcs), "sessions", window,
                       "sessions run with --layer-off (monitor 7, monitor 8, the judge and the planner off)",
                       count=len(srcs), denominator={"what": "sessions", "value": len(srcs)}))
    rows.append({"kind": "not_covered", "items": NOT_COVERED})
    rows += [{"kind": "requirement", "ref": ref, "title": title, "shown": shown, "not_shown": nshown}
             for ref, title, shown, nshown in REQUIREMENTS]
    return rows


def chain_rows(rows: list) -> list:
    out, prev = [], None
    for i, r in enumerate(rows):
        if {"seq", "prev_sha256", "row_sha256"} & set(r):
            raise ValueError(f"row {i + 1} ({r.get('kind')}) uses a chain field name: it would be overwritten")
        body = {**r, "seq": i + 1, "prev_sha256": prev}
        body["row_sha256"] = chain.row_hash(body)
        out.append(body)
        prev = body["row_sha256"]
    return out


def git_head(path: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def record(scope: Scope, kind: str) -> dict:
    rows = chain_rows(build_rows(scope, kind))
    return {"schema": SCHEMA, "scope": {"kind": kind, "sessions": [s.sid for s in scope.sessions],
                                        "window": scope.window},
            "built": {"iso": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                      "tool": "field_record.py", "v2_commit": git_head(HERE), "events_schema": events.SCHEMA},
            "rows": rows, "head": {"seq": rows[-1]["seq"], "row_sha256": rows[-1]["row_sha256"]}}


# ------------------------------------------------------------------------------------------ the CLI
def window_ids(ids: list, days: int, end: str | None) -> tuple[list, dict]:
    dates = {sid: datetime.date(int(sid[4:8]), int(sid[8:10]), int(sid[10:12])) for sid in ids}
    to = datetime.date.fromisoformat(end) if end else max(dates.values())
    frm = to - datetime.timedelta(days=days - 1)
    keep = [sid for sid in ids if frm <= dates[sid] <= to]
    return keep, {"days": days, "from": frm.isoformat(), "to": to.isoformat()}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", default=None, help="name=path (default: events.py's roots)")
    ap.add_argument("--session", action="append", default=[])
    ap.add_argument("--sessions-file", default=None)
    ap.add_argument("--window-days", type=int, default=None, help="a window record: the sessions of these days")
    ap.add_argument("--window-end", default=None, help="YYYY-MM-DD (default: the latest session's day)")
    ap.add_argument("--labels", default=str(HERE / "labels" / "labels.jsonl"))
    ap.add_argument("--windows-dir", default=str(HERE / "out" / "windows"))
    ap.add_argument("--no-windows", action="store_true")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-pages", action="store_true")
    a = ap.parse_args(argv)
    roots = {k: events.resolve(v) for k, v in events.parse_roots(a.root or list(events.DEFAULT_ROOTS)).items()}
    ids = list(a.session) + (events.read_ids(a.sessions_file) if a.sessions_file else [])
    window = None
    if a.window_days:
        ids, window = window_ids(ids, a.window_days, a.window_end)
    if not ids or (window is None and len(ids) != 1):
        print("one --session, or sessions with --window-days")
        return 2
    scope = load(roots, ids, Path(a.labels), None if a.no_windows else Path(a.windows_dir), window)
    rec = record(scope, "window" if window else "session")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "field_record.json").write_text(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
    if not a.no_pages:
        import record_pages
        (out / "field_record.html").write_text(record_pages.record_html(rec), encoding="utf-8")
        (out / "event.html").write_text(record_pages.event_html(rec, scope), encoding="utf-8")
    print(f"{rec['scope']['kind']} record: {len(rec['rows'])} rows, head {rec['head']['row_sha256'][:16]} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
