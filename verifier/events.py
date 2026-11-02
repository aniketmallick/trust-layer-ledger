#!/usr/bin/env python3
"""V2 ledger, M1: every safety event in V1's session chains, one schema (v2-ledger/event/0.1, accepted 2026-10-13).

    python3 events.py --sessions-file runs/m1_first13.txt      # -> out/events/<session>.events.jsonl + summary.json
    python3 events.py --all                                     # every session under every root

Reads V1's hash-chained session logs and never writes them. Every chain is verified first with V1's rule
(chain.py, re-implemented); a broken chain is refused, not extracted. Roots (default, from experiments/):
public = knife_handover/sessions, local = sessions_local/knife_handover; each event records its root.
Only runner sessions (KH-S*) are read; the KH-DAWN chains are dawn-protocol notes, not sessions.

One event per source row:
  freeze           a `freeze` row (goals := present). subtype m7_hand_in_workspace | m7_gate (a move refused at its
                   start) | m8_hand_link | m1..m5 by code | m6_kill_switch | m0_<code> (judge_unsafe, refused,
                   hardware, internal, motion_hours). The kill row V1 writes with an m6 or hardware freeze is linked.
  judge_veto       a `judge` row with unsafe true (the code's reading). subtype CP1 | CP2 | CP3 | FREEZE |
                   recheck_at_c; `effect` names the row that shows what the verdict held.
  planner_refusal  a `plan` row with ok false. subtype <requested>_<planner|code>; `effect` as above.
  operator_hold    an accepted [w], [l] or [k] at a freeze or release menu; the [k]'s OFF/YES key and kill row are
                   linked, never counted again. ESC / SIGINT stays a monitor-6 freeze.
  release          release_auto (auto_cp3) | an accepted [r] at the release menu (typed_release_menu) |
                   release_typed, [r] at the freeze menu (typed_freeze_menu; its key row is linked).
  override_stamp   once per session: the `override` row (operator_override), the layer-off session_start (layer_off).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import chain

SCHEMA = "v2-ledger/event/0.2"            # 0.2 (2026-10-16, M3): "clip", V1's +-3 s clip (--v2-events), or null
CLIP_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
HERE = Path(__file__).resolve().parent
EXPERIMENTS = HERE.parent
DEFAULT_ROOTS = ("public=knife_handover/sessions", "local=sessions_local/knife_handover")

METHOD_FRAME_TO_HOLD = (
    "t_hold - frame_t in ms, as V1 logs it (t_hold_minus_frame_t_ms). frame_t: time.time() in the runner's frame pump "
    "after the cv2 read and the JPEG encode of the frame the triggering hand message was computed on "
    "(frame_pump.py:96). t_hold: time.time() right after goals := present returned, retried up to 3 times 50 ms "
    "apart (motion.py:166). Includes the hand-off through /tmp/khv, the hand monitor's inference and UDP send, up to "
    "one control period (66.7 ms), the monitor evaluation and the hold write. Excludes camera exposure, readout and "
    "driver buffering, the JPEG encode, and the servos coming to rest. Defined only for monitor-7 freezes; both "
    "times are the same host's wall clock.")
METHOD_JUDGE = (
    "time.monotonic() from the start of the judge call (frame saved, context built) to the parsed verdict, the "
    "network round trip included (judge.py:158, :173); a call that reaches the 8 s budget is UNSAFE.")
METHOD_TIME = (
    "wall_iso: the event row's t_iso (IST, 1 s resolution). step_t_s: the session's monotonic clock at the last step "
    "row before the event (exact). Step rows carry no wall time; no sub-second wall time is invented.")
METHOD_GOALS = (
    "before: target6 of the last step row that sent a command. after: the freeze row's q6, the positions the firing "
    "step read; the hold writes positions it reads itself and does not log them, so 'after' is an observation, not "
    "the written goal.")
METHOD_PALM = (
    "palm = mean of landmarks 0, 1, 5, 9, 13, 17, mapped to the arm frame (mm) through the registration at an assumed "
    "palm height of 20 mm (the hand monitor's rule).")

DECISION_KINDS = ("key", "preauth", "preauth_held", "plan", "relatch", "release_auto", "release_typed", "kill",
                  "auto_retry", "resume_under_arm")
HOLD_KEYS = {"w": "wait", "l": "leave", "k": "kill_torque_off"}
MENU_STATES = ("ASK", "ASK_RELEASE")


class ChainBroken(RuntimeError):
    """A session chain fails V1's rule: it is refused, not extracted."""


# ------------------------------------------------------------------------------------------------ the session
class Session:
    """One verified chain and the indices every event needs."""

    def __init__(self, folder: Path, root: str):
        self.folder, self.root, self.sid = Path(folder), root, Path(folder).name
        ok, why, rows = chain.verify_file(self.folder / "session.jsonl")
        if not ok or not rows:
            raise ChainBroken(f"{self.sid}: {why if not ok else 'no rows'}")
        self.rows = rows
        start = next((r for r in rows if r["kind"] == "session_start"), {})
        stamp = next((r for r in rows if r["kind"] == "override"), None)
        self.meta = {"id": self.sid, "root": root, "mode": start.get("mode"),
                     "operator_override": bool(start.get("operator_override") or stamp),
                     "layer_off": bool(start.get("layer_off"))}
        self.head = rows[-1]
        self.clips, self.clip_problem = load_clips(self.folder)
        self.last_step, self.last_sent = [], []
        step = sent = None
        for i, r in enumerate(rows):                               # the step / command strictly before row i
            self.last_step.append(step)
            self.last_sent.append(sent)
            if r["kind"] == "step":
                step = i
                if r.get("target6") is not None:
                    sent = i

    def next_rows(self, i: int, stop_at_command: bool = True, limit: int = 40):
        """The rows after i, stopping at the next commanded step (the arm moving again) and at the next freeze."""
        for j in range(i + 1, min(len(self.rows), i + 1 + limit)):
            r = self.rows[j]
            if r["kind"] == "freeze" or (stop_at_command and r["kind"] == "step" and r.get("target6") is not None):
                return
            yield j, r

    def decisions_after(self, i: int, n: int = 4):
        return [(j, r) for j, r in self.next_rows(i) if r["kind"] != "step"][:n]


# ------------------------------------------------------------------------------------------------ shared blocks
def load_clips(folder: Path) -> tuple[dict, str | None]:
    """row seq -> its clip block, from <session>/v2_clips/index.jsonl (V1, --v2-events): the index verified as a chain
    first (a broken one attaches nothing), then each clip's files against their index row."""
    p = Path(folder) / "v2_clips" / "index.jsonl"
    if not p.is_file():
        return {}, None
    ok, why, rows = chain.verify_file(p)
    if not ok:
        return {}, f"the clip index is broken ({why}): no clip attached"
    return {r["row_seq"]: clip_block(Path(folder), r) for r in rows if isinstance(r.get("row_seq"), int)}, None


def clip_block(folder: Path, r: dict) -> dict:
    problems = []
    for name, want in ((r.get("mjpeg"), r.get("mjpeg_sha256")), (r.get("json"), r.get("json_sha256"))):
        f = folder / "v2_clips" / str(name)
        if not isinstance(name, str) or not CLIP_NAME.match(name):
            problems.append(f"{name!r} is not a clip file name")
        elif not f.is_file():
            problems.append(f"{name} is missing")
        elif hashlib.sha256(f.read_bytes()).hexdigest() != want:
            problems.append(f"{name} does not match its index row")
    return {"mjpeg": f"v2_clips/{r.get('mjpeg')}", "mjpeg_sha256": r.get("mjpeg_sha256"),
            "json": f"v2_clips/{r.get('json')}", "json_sha256": r.get("json_sha256"), "event": r.get("event"),
            "n_frames": r.get("n_frames"), "dt_first_s": r.get("dt_first_s"), "dt_last_s": r.get("dt_last_s"),
            "truncated": r.get("truncated"), "index_seq": r.get("seq"), "index_row_sha256": r.get("row_sha256"),
            "verified": not problems, "problem": "; ".join(problems) or None}


def envelope(s: Session, i: int, etype: str, subtype: str) -> dict:
    r = s.rows[i]
    st = s.rows[s.last_step[i]] if s.last_step[i] is not None else None
    return {
        "schema": SCHEMA, "event_id": f"{s.sid}#{r['seq']}", "type": etype, "subtype": subtype,
        "session": dict(s.meta), "trial_id": r.get("trial_id"),
        "source": {"chain": f"{s.sid}/session.jsonl", "row_seq": r["seq"], "row_kind": r["kind"],
                   "row_sha256": r["row_sha256"], "prev_sha256": r["prev_sha256"],
                   "chain_head_seq": s.head["seq"], "chain_head_sha256": s.head["row_sha256"]},
        "step": None if st is None else {"seq": st["seq"], "k": st.get("k"), "t_s": st.get("t_s"),
                                         "phase": st.get("phase")},
        "time": {"wall_iso": r.get("t_iso"), "step_t_s": None if st is None else st.get("t_s"), "method": METHOD_TIME},
        "monitors": None if st is None else st.get("monitors"),
        "clip": None if etype == "override_stamp" else s.clips.get(r["seq"]),
    }


def step_hands(s: Session, i: int) -> dict:
    st = s.rows[s.last_step[i]] if s.last_step[i] is not None else {}
    m7 = (st.get("monitors") or {}).get("7") or {}
    palms = st.get("hands_mm")
    return {"n": m7.get("n_hands"), "palms_mm": palms, "hand_seq": st.get("hand_seq"), "frame_seq": st.get("frame_seq"),
            "source": "step hands_mm" if palms else "step monitor-7 count (no palms on this step: palms are logged "
                      "per step only since 2026-10-04 ~14:30, and only while a hand is seen)",
            "method": METHOD_PALM}


def goals(s: Session, i: int, after=None, held=None, attempts=None) -> dict:
    k = s.last_sent[i]
    sent = None if k is None else s.rows[k]
    return {"before": None if sent is None else sent["target6"], "before_seq": None if sent is None else sent["seq"],
            "after": after, "held": held, "hold_attempts": attempts, "method": METHOD_GOALS}


def judge_block(r: dict) -> dict:
    v = r.get("verdict") or {}
    return {"seq": r["seq"], "phase": r.get("phase"), "rubric": v.get("rubric", "checkpoint"),
            "rubric_sha256": v.get("rubric_sha256"), "model": v.get("model"), "recommend": v.get("recommend"),
            "p_unsafe": v.get("p_unsafe"), "unsafe_by_code": bool(r.get("unsafe")), "latency_s": v.get("latency_s"),
            "latency_method": METHOD_JUDGE}


def decision(r: dict) -> dict:
    out = {"seq": r["seq"], "kind": r["kind"]}
    for k in ("key", "by", "state", "refused", "requested", "why", "cause"):
        if r.get(k) not in (None, ""):
            out[k] = r[k]
    if r["kind"] == "plan":
        out["ok"] = bool((r.get("plan") or {}).get("ok"))
    return out


def own_kill(s: Session, i: int) -> dict | None:
    """The kill row the runner wrote itself right after the freeze at i (hardware, SIGINT): one before any key row.
    A kill that follows the operator's keys belongs to the [k] that led to it, never to the freeze as well."""
    for _, rr in s.next_rows(i, limit=3):
        if rr["kind"] == "key":
            return None
        if rr["kind"] == "kill":
            return rr
    return None


def confirmed_kill(s: Session, i: int) -> tuple[dict | None, dict | None]:
    """The [k] at row i: (its KILL confirmation, the kill row it led to). The confirmation is the next key row; the
    kill row counts only after an accepted confirmation and before any other key (a mistyped one leads to none)."""
    conf = None
    for _, rr in s.next_rows(i, limit=6):
        if rr["kind"] == "key":
            if conf is not None or rr.get("state") != "KILL":
                break
            conf = rr
            if not rr.get("accepted"):
                break
        elif rr["kind"] == "kill" and conf is not None:
            return conf, rr
    return conf, None


def no_latency(why: str) -> dict:
    return {"frame_to_hold_ms": None, "why": why, "method": METHOD_FRAME_TO_HOLD}


# ------------------------------------------------------------------------------------------------ the six types
def freeze_subtype(r: dict) -> str:
    n, code, src = r.get("monitor"), str(r.get("code")), str(r.get("source") or "")
    if n == 7:
        return "m7_gate" if src.startswith("gate:") else "m7_hand_in_workspace"
    if n == 8:
        return "m8_hand_link"
    return f"m{n}_{code}"


def ev_freeze(s: Session, i: int) -> dict:
    r = s.rows[i]
    e = envelope(s, i, "freeze", freeze_subtype(r))
    e["trigger"] = {k: r.get(k) for k in ("monitor", "code", "source", "detail", "fired")}
    hands = r.get("hands") or []
    e["hands"] = {"n": len(hands), "palms_mm": [h.get("palm_mm") for h in hands],
                  "handedness": [h.get("handedness") for h in hands], "conf": [h.get("conf") for h in hands],
                  "in_workspace": [h.get("in_workspace") for h in hands], "hand_seq": r.get("hand_seq"),
                  "frame_seq": r.get("frame_seq"), "source": "freeze row hands", "method": METHOD_PALM}
    e["goals"] = goals(s, i, r.get("q6"), r.get("held"), r.get("hold_attempts"))
    if r.get("monitor") == 7 and r.get("t_hold_minus_frame_t_ms") is not None:
        e["latency"] = {"frame_to_hold_ms": r["t_hold_minus_frame_t_ms"], "method": METHOD_FRAME_TO_HOLD}
    else:
        e["latency"] = no_latency("defined only for monitor-7 freezes (the trigger is not a camera frame)"
                                  if r.get("monitor") != 7 else "the firing message carried no frame_t")
    jr = next((rr for _, rr in s.next_rows(i) if rr["kind"] == "judge"), None)
    e["judge"] = None if jr is None else judge_block(jr)
    e["photo"] = None if not r.get("photo") else {"file": r["photo"], "sha256": r.get("photo_sha256"),
                                                  "is_the_trigger_frame": bool(r.get("photo_is_the_message_frame"))}
    e["followed_by"] = [decision(rr) for _, rr in s.next_rows(i) if rr["kind"] in DECISION_KINDS][:6]
    kill = own_kill(s, i)
    e["kill_row_seq"] = None if kill is None else kill["seq"]
    return e


def veto_subtype(s: Session, i: int) -> str:
    r = s.rows[i]
    nxt = next((rr for _, rr in s.next_rows(i, limit=4) if rr["kind"] != "step"), {})
    frame = (r.get("verdict") or {}).get("frame_sha256") or r.get("frame_sha256")
    fresh = (nxt.get("fresh_verdict") or {}).get("frame_sha256")
    if nxt.get("kind") == "key" and nxt.get("key") == "c" and fresh == frame:
        return "recheck_at_c"
    return str(r.get("phase"))


def veto_effect(s: Session, i: int) -> dict:
    for _, rr in s.decisions_after(i, 5):
        kind = rr["kind"]
        if kind == "preauth_held":
            return {"kind": "preauth_held", "seq": rr["seq"]}
        if kind == "key" and rr.get("refused"):
            return {"kind": "key_refused", "seq": rr["seq"]}
        if kind == "key" and rr.get("state") == "ASK_RELEASE":
            return {"kind": "release_menu", "seq": rr["seq"]}
        if kind == "relatch":
            return {"kind": "relatch", "seq": rr["seq"]}
    nxt = next((j for j in range(i + 1, min(len(s.rows), i + 4)) if s.rows[j]["kind"] == "freeze"), None)
    if nxt is not None and s.rows[nxt].get("code") == "judge_unsafe":
        return {"kind": "freeze", "seq": s.rows[nxt]["seq"]}
    return {"kind": "none_found", "seq": None}


def ev_judge_veto(s: Session, i: int) -> dict:
    r = s.rows[i]
    e = envelope(s, i, "judge_veto", veto_subtype(s, i))
    e["judge"] = judge_block(r)
    e["context"] = r.get("context")
    e["effect"] = veto_effect(s, i)
    e["hands"] = step_hands(s, i)
    e["goals"] = goals(s, i)
    e["latency"] = no_latency("a judge call: see judge.latency_s")
    frame = r.get("frame")
    e["photo"] = None if not frame else {"file": frame, "sha256": r.get("frame_sha256"), "is_the_trigger_frame": True}
    e["followed_by"] = [decision(rr) for _, rr in s.decisions_after(i, 6) if rr["kind"] in DECISION_KINDS]
    return e


def ev_planner_refusal(s: Session, i: int) -> dict:
    r = s.rows[i]
    p = r.get("plan") or {}
    by = "code" if r.get("refused_by") else "planner"
    e = envelope(s, i, "planner_refusal", f"{r.get('requested')}_{by}")
    e["plan"] = {"requested": r.get("requested"), "refused_by": by, "reason": p.get("reason"),
                 "approved_palm_mm": r.get("approved_palm_mm"), "fingers_dir": r.get("fingers_dir"),
                 "plan_ms": r.get("plan_ms")}
    effect = {"kind": "none_found", "seq": None}
    for _, rr in s.decisions_after(i, 3):
        if rr["kind"] in ("auto_retry", "relatch"):
            effect = {"kind": rr["kind"], "seq": rr["seq"]}
            break
    else:
        nxt = next((j for j in range(i + 1, min(len(s.rows), i + 4)) if s.rows[j]["kind"] == "freeze"), None)
        if nxt is not None and s.rows[nxt].get("code") == "refused":
            effect = {"kind": "freeze_refused", "seq": s.rows[nxt]["seq"]}
    e["effect"] = effect
    e["hands"] = step_hands(s, i)
    e["goals"] = goals(s, i)
    return e


def ev_operator_hold(s: Session, i: int) -> dict:
    r = s.rows[i]
    e = envelope(s, i, "operator_hold", HOLD_KEYS[r["key"]])
    e["key"] = {k: r.get(k) for k in ("key", "state", "menu", "verdict_on_screen", "cause")}
    e["goals"] = goals(s, i)
    if r["key"] == "k":
        conf, kill = confirmed_kill(s, i)
        e["kill_key_seq"] = None if conf is None else conf["seq"]
        e["kill_row_seq"] = None if kill is None else kill["seq"]
        e["torque_off_confirmed"] = kill is not None
    return e


def ev_release(s: Session, i: int, subtype: str) -> dict:
    r = s.rows[i]
    e = envelope(s, i, "release", subtype)
    if subtype == "auto_cp3":
        e["released_by"], e["verdict"] = "the CP3 verdict (not UNSAFE), pre-authorised", r.get("verdict")
        cp3 = next((s.rows[j] for j in range(i - 1, max(-1, i - 40), -1) if s.rows[j]["kind"] == "judge"), None)
        e["cp3_context"] = None if cp3 is None else cp3.get("context")
    elif subtype == "typed_release_menu":
        e["released_by"], e["verdict"], e["cp3_context"] = "[r] at the release menu", r.get("verdict_on_screen"), None
    else:
        key = next((s.rows[j] for j in range(i - 1, max(-1, i - 4), -1) if s.rows[j]["kind"] == "key"
                    and s.rows[j].get("key") == "r"), None)
        e["released_by"], e["verdict"], e["cp3_context"] = "[r] at the freeze menu", r.get("verdict"), None
        e["key_seq"] = None if key is None else key["seq"]
        e["on_descent_row"], e["palm_mm"] = r.get("on_descent_row"), r.get("palm_mm")
    later = [rr for rr in s.rows[i + 1:i + 400] if rr["kind"] in ("wait_clear", "outcome_label", "trial_end")]
    upto = next((n for n, rr in enumerate(later) if rr["kind"] == "trial_end"), len(later))
    e["wait_clear"] = next(({k: rr.get(k) for k in ("seq", "frames", "frames_seen", "resets", "waited_s")}
                            for rr in later[:upto] if rr["kind"] == "wait_clear"), None)
    e["outcome_label"] = next(({k: rr.get(k) for k in ("seq", "received", "released_by")}
                               for rr in later[:upto + 1] if rr["kind"] == "outcome_label"), None)
    e["hands"] = step_hands(s, i)
    e["goals"] = goals(s, i)
    return e


def ev_override(s: Session, i: int, subtype: str) -> dict:
    r = s.rows[i]
    e = envelope(s, i, "override_stamp", subtype)
    if subtype == "operator_override":
        e["stamp"], e["bypassed"] = r.get("what"), r.get("bypassed")
        e["stamped_rows"] = sum(1 for rr in s.rows if rr["kind"] == "step" and rr.get("override"))
    else:
        e["stamp"], e["bypassed"] = r.get("layer_off"), None
        e["stamped_rows"] = sum(1 for rr in s.rows if rr.get("trust_layer"))
    return e


# ------------------------------------------------------------------------------------------------ extraction
def events_of(s: Session) -> list[dict]:
    out, seen_override = [], False
    for i, r in enumerate(s.rows):
        kind = r["kind"]
        if kind == "freeze":
            out.append(ev_freeze(s, i))
        elif kind == "judge" and r.get("unsafe"):
            out.append(ev_judge_veto(s, i))
        elif kind == "plan" and not (r.get("plan") or {}).get("ok"):
            out.append(ev_planner_refusal(s, i))
        elif kind == "key" and r.get("accepted") and r.get("state") in MENU_STATES:
            if r.get("key") in HOLD_KEYS:
                out.append(ev_operator_hold(s, i))
            elif r.get("key") == "r" and r.get("state") == "ASK_RELEASE":
                out.append(ev_release(s, i, "typed_release_menu"))
        elif kind == "release_auto":
            out.append(ev_release(s, i, "auto_cp3"))
        elif kind == "release_typed":
            out.append(ev_release(s, i, "typed_freeze_menu"))
        elif kind == "override" and not seen_override:
            seen_override = True
            out.append(ev_override(s, i, "operator_override"))
        elif kind == "session_start" and r.get("layer_off"):
            out.append(ev_override(s, i, "layer_off"))
    return out


def extract_session(folder: Path, root: str) -> list[dict]:
    """Every event of one session folder (its chain verified first). Raises ChainBroken."""
    return events_of(Session(folder, root))


# ------------------------------------------------------------------------------------------------ the CLI
def parse_roots(specs: list[str]) -> dict:
    roots = {}
    for spec in specs:
        name, _, path = spec.partition("=")
        if not name or not path:
            raise SystemExit(f"--root takes name=path, not {spec!r}")
        roots[name] = path
    return roots


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else EXPERIMENTS / p


def find_sessions(roots: dict, wanted: list[str] | None) -> list[tuple[str, str, Path]]:
    found: dict = {}
    for name, path in roots.items():
        for d in sorted(resolve(path).glob("KH-S*")):
            if (d / "session.jsonl").is_file():
                if d.name in found:
                    raise SystemExit(f"{d.name} is under two roots ({found[d.name][0]}, {name})")
                found[d.name] = (name, d)
    if wanted is None:
        return [(sid, n, d) for sid, (n, d) in sorted(found.items())]
    missing = [w for w in wanted if w not in found]
    if missing:
        raise SystemExit(f"not found under any root: {', '.join(missing)}")
    return [(sid, *found[sid]) for sid in wanted]


def read_ids(path: str) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [x.strip() for x in lines if x.strip() and not x.startswith("#")]


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", default=None, help="name=path (from experiments/ if relative)")
    ap.add_argument("--sessions-file", default=None)
    ap.add_argument("--session", action="append", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=str(HERE / "out" / "events"))
    a = ap.parse_args(argv)
    roots = parse_roots(a.root or list(DEFAULT_ROOTS))
    wanted = None if a.all else (a.session or []) + (read_ids(a.sessions_file) if a.sessions_file else [])
    if wanted == []:
        raise SystemExit("name sessions: --session, --sessions-file, or --all")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = {"schema": SCHEMA, "generated_iso": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
               "roots": roots, "sessions": {}, "refused": {}}
    totals_t, totals_s = Counter(), Counter()
    for sid, root, folder in find_sessions(roots, wanted):
        try:
            s = Session(folder, root)
        except ChainBroken as e:
            summary["refused"][sid] = str(e)
            continue
        evs = events_of(s)
        (out / f"{sid}.events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs),
                                                encoding="utf-8")
        by_t, by_s = Counter(e["type"] for e in evs), Counter(f"{e['type']}/{e['subtype']}" for e in evs)
        totals_t.update(by_t)
        totals_s.update(by_s)
        clips = [e["clip"] for e in evs if e.get("clip")]
        summary["sessions"][sid] = {"root": root, "rows": len(s.rows), "chain_head_sha256": s.head["row_sha256"],
                                    "events": len(evs), "by_type": dict(by_t), "by_subtype": dict(sorted(by_s.items())),
                                    "clips": {"attached": len(clips), "verified": sum(c["verified"] for c in clips),
                                              "problem": s.clip_problem}}
    summary["totals"] = {"sessions": len(summary["sessions"]), "events": sum(totals_t.values()),
                         "by_type": dict(totals_t), "by_subtype": dict(sorted(totals_s.items()))}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{summary['totals']['events']} events from {summary['totals']['sessions']} sessions -> {out}"
          + (f"; REFUSED (chain broken): {', '.join(summary['refused'])}" if summary["refused"] else ""))
    return 1 if summary["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
