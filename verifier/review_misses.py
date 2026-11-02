#!/usr/bin/env python3
"""V2 ledger, M2: candidate windows for the miss review.

A miss cannot come from events: it is a moment the layer did not fire and should have. This tool picks the moments
worth a look from V1's step rows, using monitor 7's own state only to choose where to look (never as the answer),
samples them with a fixed seed, and writes each as a window event that label.py shows and labels
(miss / correct_no_fire / unclear). Every window and the summary carry the sampling method.

Categories (moving steps only; none within FREEZE_GUARD_S of a freeze, where the layer did fire):
  A  a hand seen but not in the workspace while the arm moved with no hand allowed (exclusion phases).
     Palm era: a palm within MARGIN_MM of the workspace box. Count era: monitor 7 counted a hand.
  B  the hand's track dropped (1+ hands, then 0) between two moving steps of exclusion phases
  C  the approved hand unseen, or its reading not used, while the arm moved toward it (monitor 7's forgiving branches:
     under the arm, flicker, dropout), approved phases
  D  no hand signal at all while the arm moved, exclusion phases: the random pool, so the sample is not only where
     the system saw something
  E  a judge call that saw a hand the hand monitor did not (verdict hand_present, monitor count 0)
Sample: every episode of A, B and E; every C episode with a stored frame within FRAME_S of it (plus --c-unframed at
random); --d-random steps at random from the D pool. Sessions where monitor 7 was not read (--layer-off) are excluded.
Era: "palms" for sessions from 2026-10-04 14:30 (V1 logs palms per step from then, while a hand is seen), else
"count" (monitor 7's hand count and its 5-frame window only).

    python3 review_misses.py --sessions-file runs/m1_first13.txt     # -> out/windows/*.events.jsonl, summary.json
    python3 label.py --events-dir out/windows                        # the review
"""
from __future__ import annotations

import argparse
import datetime
import json
import random
import sys
from collections import Counter
from pathlib import Path

import chain
import events

SCHEMA = "v2-ledger/window/0.1"
HERE = Path(__file__).resolve().parent
# V1's monitor-7 phase sets (knife_handover/monitors.py); tests/test_review_misses.py checks them when V1 is present
MOVING = frozenset({"GRASP", "LIFT", "APPROACH_ZONE", "PRE_PLACE", "PLACE", "RETREAT", "REORIENT", "HANDOVER_APPROACH",
                    "RELEASE", "SETUP", "PLACE_APPROACH", "PLACE_DESCENT", "PLACE_LIFTOFF", "POLICY"})
APPROVED_PHASES = ("HANDOVER_APPROACH", "REORIENT", "PLACE_APPROACH", "PLACE_DESCENT", "PLACE_LIFTOFF")
OFF_PREFIX = "OFF: the trust layer is off"
NO_HAND_IN_WS = "no hand in the workspace"
FORGIVEN = ("the approved hand under the arm", "the approved hand unseen for", "the approved hand not in this frame")
# The G3 workspace box (arm frame, mm, margin included) of registration r2 (9fb34cea93f5), as V1's TableMap reads it
BOX_XY = (171.9, 368.6, -166.2, 143.0)
PALMS_SINCE = "20261004T1430"
WINDOW_S, FREEZE_GUARD_S, FRAME_S, MARGIN_MM, SEED = 3.0, 3.0, 5.0, 50.0, 20261015
CATEGORIES = {"A": "hand_seen_outside_the_workspace", "B": "track_dropped", "C": "approved_hand_unseen_forgiven",
              "D": "no_hand_signal", "E": "judge_saw_a_hand_the_monitor_did_not"}
METHOD_WINDOW = (f"window: the step rows {WINDOW_S:g} s either side of the anchor (the first step of an episode, "
                 f"a random step of the D pool, or the step before a judge row), on the session's monotonic clock; "
                 f"frames: stored freeze photos and judge frames within {FRAME_S:g} s of the anchor, placed on that "
                 f"clock by the last step before their row (1 step of error); wall time from the nearest earlier row "
                 f"(1 s).")
METHOD_SAMPLING = (f"population: episodes (runs of consecutive moving steps) of categories A-D and judge rows of E, "
                   f"from the sessions listed, excluding moving steps within {FREEZE_GUARD_S:g} s of a freeze and "
                   f"sessions where monitor 7 was not read; sample: all of A, B and E, the C episodes with a frame "
                   f"within {FRAME_S:g} s (plus c_unframed at random), d_random steps at random from the D pool; "
                   f"random draws seeded (seed in the summary). Monitor 7's state chose where to look; it is not the "
                   f"label.")


class Session:
    def __init__(self, folder: Path, root: str):
        ok, why, rows = chain.verify_file(Path(folder) / "session.jsonl")
        if not ok or not rows:
            raise events.ChainBroken(f"{folder.name}: {why}")
        self.folder, self.root, self.sid, self.rows = Path(folder), root, folder.name, rows
        self.head = rows[-1]
        steps = [r for r in rows if r["kind"] == "step"]
        m7_off = bool(steps) and all(str(m7(r).get("detail", "")).startswith(OFF_PREFIX) for r in steps)
        self.era = "m7 off" if m7_off else ("palms" if self.sid[4:17] >= PALMS_SINCE else "count")
        self.freeze_times, self.frames, last = [], [], None
        for r in rows:
            if r["kind"] == "step":
                last = r
            elif last is not None and r["kind"] == "freeze":
                self.freeze_times.append(last["t_s"])
            f = r.get("frame") or r.get("photo")
            if last is not None and r["kind"] in ("judge", "freeze") and f:
                self.frames.append({"file": f, "kind": r["kind"], "seq": r["seq"], "t_s": last["t_s"],
                                    "sha256": r.get("frame_sha256") or r.get("photo_sha256")})


def load(folder: Path, root: str) -> Session:
    return Session(Path(folder), root)


def m7(step: dict) -> dict:
    return (step.get("monitors") or {}).get("7") or {}


def box_dist(p) -> float:
    x0, x1, y0, y1 = BOX_XY
    dx, dy = max(x0 - p[0], 0.0, p[0] - x1), max(y0 - p[1], 0.0, p[1] - y1)
    return (dx * dx + dy * dy) ** 0.5


def step_categories(s: Session, r: dict, prev_n: int) -> set:
    ph, mm = r.get("phase"), m7(r)
    n, det = mm.get("n_hands") or 0, str(mm.get("detail") or "")
    if ph not in MOVING or r.get("target6") is None or any(abs(r["t_s"] - t) <= FREEZE_GUARD_S for t in s.freeze_times):
        return set()
    cats = set()
    exclusion = ph not in APPROVED_PHASES and ph != "RELEASE"
    if exclusion and n > 0 and det.startswith(NO_HAND_IN_WS):
        if s.era == "count" or any(box_dist(p) <= MARGIN_MM for p in (r.get("hands_mm") or [])):
            cats.add("A")
    if exclusion and prev_n > 0 and n == 0:
        cats.add("B")
    if det.startswith(FORGIVEN):
        cats.add("C")
    if exclusion and n == 0:
        cats.add("D")
    return cats


def survey_session(s: Session) -> dict:
    """category -> episodes: runs of consecutive candidate steps (A-D) or judge rows (E). Empty with monitor 7 off."""
    if s.era == "m7 off":
        return {}
    out: dict = {}
    open_run: dict = {}
    prev_n, last_step = 0, None
    for i, r in enumerate(s.rows):
        if r["kind"] == "judge":
            v, c = r.get("verdict") or {}, r.get("context") or {}
            if v.get("hand_present") and c.get("hands_seen_by_monitor") == 0 and last_step is not None:
                out.setdefault("E", []).append({"kind": "judge", "category": "E", "row_i": i, "row_kind": "judge",
                                                "t_s": last_step["t_s"], "step": last_step, "phase": r.get("phase"),
                                                "steps": 0})
            continue
        if r["kind"] != "step":
            continue
        cats = step_categories(s, r, prev_n)
        for c in list(open_run):
            if c not in cats:
                del open_run[c]
        for c in cats:
            if c in open_run:
                open_run[c]["steps"] += 1
                open_run[c]["step_ids"].append(i)
            else:
                ep = {"kind": "step_run", "category": c, "row_i": i, "row_kind": "step", "t_s": r["t_s"], "step": r,
                      "phase": r.get("phase"), "steps": 1, "step_ids": [i]}
                open_run[c] = ep
                out.setdefault(c, []).append(ep)
        excl_moving = r.get("phase") in MOVING and r.get("phase") not in APPROVED_PHASES and r.get("phase") != "RELEASE"
        prev_n = (m7(r).get("n_hands") or 0) if excl_moving and r.get("target6") is not None else 0
        last_step = r
    return out


def frames_near(s: Session, t_s: float) -> list[dict]:
    near = [{**f, "dt_s": round(f["t_s"] - t_s, 2)} for f in s.frames if abs(f["t_s"] - t_s) <= FRAME_S]
    return sorted(near, key=lambda f: abs(f["dt_s"]))


def wall_iso(s: Session, i: int) -> str | None:
    return next((r["t_iso"] for r in reversed(s.rows[:i + 1]) if r.get("t_iso")), None)


def window_event(s: Session, ep: dict, anchor_i: int, sampling: dict) -> dict:
    r, st = s.rows[anchor_i], ep["step"] if ep["kind"] == "judge" else s.rows[anchor_i]
    t0 = st["t_s"]
    near = frames_near(s, t0)
    span = [x for x in s.rows if x["kind"] == "step" and abs(x["t_s"] - t0) <= WINDOW_S]
    cat = ep["category"]
    return {
        "schema": SCHEMA, "event_id": f"{s.sid}@{r['seq']}", "type": "window", "subtype": f"{cat}_{CATEGORIES[cat]}",
        "session": {"id": s.sid, "root": s.root, "era": s.era}, "trial_id": r.get("trial_id"),
        "source": {"chain": f"{s.sid}/session.jsonl", "row_seq": r["seq"], "row_kind": r["kind"],
                   "row_sha256": r["row_sha256"], "prev_sha256": r["prev_sha256"], "chain_head_seq": s.head["seq"],
                   "chain_head_sha256": s.head["row_sha256"]},
        "step": {"seq": st["seq"], "k": st.get("k"), "t_s": t0, "phase": st.get("phase")},
        "time": {"wall_iso": wall_iso(s, anchor_i), "step_t_s": t0, "method": METHOD_WINDOW},
        "monitors": st.get("monitors"),
        "window": {"category": cat, "category_text": CATEGORIES[cat], "era": s.era, "from_s": round(t0 - WINDOW_S, 3),
                   "to_s": round(t0 + WINDOW_S, 3), "episode_steps": ep["steps"], "steps_in_window": len(span),
                   "hands_max": max((m7(x).get("n_hands") or 0 for x in span), default=0),
                   "m7_details": dict(Counter(str(m7(x).get("detail", ""))[:40] for x in span).most_common(4)),
                   "frames": [{k: f[k] for k in ("file", "kind", "seq", "dt_s")} for f in near],
                   "method": METHOD_WINDOW},
        "photo": None if not near else {"file": near[0]["file"], "sha256": near[0]["sha256"],
                                        "is_the_trigger_frame": False, "dt_s": near[0]["dt_s"]},
        "sampling": sampling,
    }


def sample(surveys: dict, d_random: int, c_unframed: int, seed: int) -> list[tuple]:
    """[(session, episode, anchor row index, sampling block)] in a fixed order for a fixed seed."""
    rng, out = random.Random(seed), []
    pops: dict = {}
    for s, cands in surveys:
        for c, eps in cands.items():
            pops.setdefault((s.era, c), []).extend((s, ep) for ep in eps)
    for (era, c), items in sorted(pops.items(), key=lambda kv: kv[0]):
        if c in "ABE":
            picked = [(s, ep, ep["row_i"]) for s, ep in items]
            rule = "every episode"
        elif c == "C":
            framed = [(s, ep, ep["row_i"]) for s, ep in items if frames_near(s, ep["t_s"])]
            unframed = [(s, ep, ep["row_i"]) for s, ep in items if not frames_near(s, ep["t_s"])]
            picked = framed + rng.sample(unframed, min(c_unframed, len(unframed)))
            rule = f"every episode with a frame within {FRAME_S:g} s, plus {c_unframed} unframed at random"
        else:
            continue
        out += [(s, ep, i, {"category": c, "era": era, "population": len(items), "sampled": len(picked), "rule": rule,
                            "seed": seed}) for s, ep, i in picked]
    d_pool = [(s, ep, i) for (era, c), items in sorted(pops.items(), key=lambda kv: kv[0]) if c == "D"
              for s, ep in items for i in ep["step_ids"]]
    for s, ep, i in rng.sample(d_pool, min(d_random, len(d_pool))):
        out.append((s, {**ep, "steps": 1}, i, {"category": "D", "era": s.era, "population": len(d_pool),
                                                "population_unit": "steps", "sampled": min(d_random, len(d_pool)),
                                                "rule": "steps at random from the D pool (all eras)", "seed": seed}))
    return out


def summarise(surveys: list, picked: list, excluded: dict, roots: dict, a: argparse.Namespace) -> dict:
    pops: dict = {}
    for s, cands in surveys:
        for c, eps in cands.items():
            p = pops.setdefault(s.era, {}).setdefault(c, {"episodes": 0, "framed": 0, "steps": 0})
            p["episodes"] += len(eps)
            p["steps"] += sum(ep["steps"] for ep in eps)
            p["framed"] += sum(1 for ep in eps if frames_near(s, ep["t_s"]))
    sampled: dict = {}
    for s, ep, i, smp in picked:
        sampled.setdefault(s.era, Counter())[smp["category"]] += 1
    return {"schema": SCHEMA, "generated_iso": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "roots": roots, "seed": a.seed,
            "parameters": {"window_s": WINDOW_S, "freeze_guard_s": FREEZE_GUARD_S, "frame_s": FRAME_S,
                           "margin_mm": MARGIN_MM, "box_xy": BOX_XY, "palms_since": PALMS_SINCE,
                           "d_random": a.d_random, "c_unframed": a.c_unframed},
            "sessions": {s.sid: {"root": s.root, "era": s.era, "rows": len(s.rows),
                                 "chain_head_sha256": s.head["row_sha256"]} for s, _ in surveys},
            "excluded": excluded, "populations": pops, "sampled": {k: dict(v) for k, v in sampled.items()},
            "categories": CATEGORIES, "method": METHOD_SAMPLING}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", default=None, help="name=path (default: events.py's roots)")
    ap.add_argument("--sessions-file", default=None)
    ap.add_argument("--session", action="append", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=str(HERE / "out" / "windows"))
    ap.add_argument("--d-random", type=int, default=10)
    ap.add_argument("--c-unframed", type=int, default=0)
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args(argv)
    roots = events.parse_roots(a.root or list(events.DEFAULT_ROOTS))
    wanted = None if a.all else (a.session or []) + (events.read_ids(a.sessions_file) if a.sessions_file else [])
    surveys, excluded = [], {}
    for sid, root, folder in events.find_sessions(roots, wanted):
        s = load(folder, root)
        if s.era == "m7 off":
            excluded[sid] = "m7 off: the hand monitor was not read (--layer-off)"
            continue
        surveys.append((s, survey_session(s)))
    picked = sample(surveys, a.d_random, a.c_unframed, a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.events.jsonl"):
        old.unlink()                                        # generated output only: a new sample replaces the old
    by_sid: dict = {}
    for s, ep, i, smp in picked:
        by_sid.setdefault(s.sid, []).append(window_event(s, ep, i, smp))
    for sid, ws in by_sid.items():
        ws.sort(key=lambda w: w["source"]["row_seq"])
        (out / f"{sid}.events.jsonl").write_text("".join(json.dumps(w, sort_keys=True) + "\n" for w in ws),
                                                 encoding="utf-8")
    summary = summarise(surveys, picked, excluded, roots, a)
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(picked)} windows from {len(surveys)} sessions ({len(excluded)} excluded) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
