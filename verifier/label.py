#!/usr/bin/env python3
"""V2 ledger, M1: the labelling tool. Each labelable event is shown (the decision rows around it, the steps 3 s either
side, the freeze photo when there is one) and asked one question; the answer is appended to labels/labels.jsonl, a
hash chain with V1's rule (chain.py). Nothing is ever overwritten: a correction is a new row naming the row it
supersedes. A label counts only for the V1 row it was given for (its event_row_sha256).

    python3 label.py                              # the next unlabelled events, session by session
    python3 label.py --session KH-S... --type freeze
    python3 label.py --event KH-S20261005T015041#806   # one event; already labelled -> the answer is a correction
    python3 label.py --coverage                   # labelled / labelable per type; nothing is asked
    python3 label.py --proposals labels/proposals_20261014.jsonl   # show a model's proposal with each event
    python3 label.py --proposals labels/proposals_20261014.jsonl --confidence low   # only the low-confidence ones
    python3 label.py --recheck bulk,model_panel --type release   # make the bulk / panel labels your own, one by one

A proposal (a hash-chained row: label, confidence, reason, by) is shown with its event; Enter takes it, any answer key
overrides it. It is never a label by itself: the label row is the labeller's, and it records the proposal it was shown
and whether the label equals it, so every number can say how many labels took a proposal unchanged.
--accept-proposals --bulk-note "why" writes the queued proposals as labels without showing the events; each such row
says review "bulk" and why, and every count splits labels made one by one from labels accepted in bulk.

Questions (shown with every event):
  freeze, judge_veto, planner_refusal - did it fire for a reason?
      true_positive  it stopped or held the arm and something warranted it
      false_alarm    it stopped or held the arm and nothing warranted it
      unclear        the record does not show which
  release - "Released into an open, ready hand?"  yes / no / unclear
  operator_hold, override_stamp - counted, not labelled.  `miss` exists only on M2 review windows, never on an event.
Input: events from events.py (out/events/*.events.jsonl); the V1 chains through the same roots, for the display.
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import chain
import events

HERE = Path(__file__).resolve().parent
QUESTIONS = {
    "fired_for_a_reason": {
        "text": "Did it fire for a reason?",
        "help": ["true_positive  it stopped or held the arm and something warranted it",
                 "false_alarm    it stopped or held the arm and nothing warranted it",
                 "unclear        the record does not show which"],
        "keys": {"t": "true_positive", "f": "false_alarm", "u": "unclear"}},
    "released_into_ready_hand": {
        "text": "Released into an open, ready hand?",
        "help": ["yes / no / unclear"],
        "keys": {"y": "yes", "n": "no", "u": "unclear"}},
    "miss_review": {
        "text": "Was this a miss?",
        "help": ["miss             a hand was where the layer should have stopped the arm (or the approved hand had "
                 "gone), and it did not",
                 "correct_no_fire  nothing in the window needed a stop",
                 "unclear          the record does not show which"],
        "keys": {"m": "miss", "c": "correct_no_fire", "u": "unclear"}},
}
QUESTION_OF_TYPE = {"freeze": "fired_for_a_reason", "judge_veto": "fired_for_a_reason",
                    "planner_refusal": "fired_for_a_reason", "release": "released_into_ready_hand",
                    "window": "miss_review"}
REVIEW_KINDS = ("one_by_one", "bulk", "model_panel")    # how a label was made; rows before the field: one by one
METHOD_COVERAGE = ("labelable: the events of a labelled type in the events directory. labelled: those whose newest "
                   "label row in the labels chain names the same V1 row (event_row_sha256). The rest are unreviewed.")
STEP_WINDOW_S, STEP_EVERY = 3.0, 3                     # the steps 3 s either side, every 3rd (15 Hz -> 5 Hz)
ROWS_BEFORE, ROWS_AFTER = 5, 8                         # decision rows (steps skipped) either side of the event
RELEASE_FRAME_ROWS = 60                                # how far back a release looks for the last judge frame
AFTER_S = 10.0                                         # how long after a release its first stored frame may be


def question_for(etype: str) -> str | None:
    return QUESTION_OF_TYPE.get(etype)


# ------------------------------------------------------------------------------------------------ data
def load_events(events_dir: Path) -> list[dict]:
    out = []
    for p in sorted(Path(events_dir).glob("*.events.jsonl")):
        out += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return sorted(out, key=lambda e: (e["session"]["id"], e["source"]["row_seq"]))


def latest_labels(rows: list[dict]) -> dict:
    """event_id -> its newest label row (a correction supersedes the rows before it)."""
    return {r["event_id"]: r for r in rows if r.get("kind") == "label"}


def bound(lab: dict | None, ev: dict) -> bool:
    """The label was given for this event's V1 row."""
    return lab is not None and lab.get("event_row_sha256") == ev["source"]["row_sha256"]


def unbound(evs: list[dict], latest: dict) -> list[str]:
    """Events whose newest label names another V1 row: counted as unreviewed, asked again."""
    return [e["event_id"] for e in evs if e["event_id"] in latest and not bound(latest[e["event_id"]], e)]


def coverage(evs: list[dict], latest: dict) -> dict:
    cov: dict = {}
    for e in evs:
        if question_for(e["type"]) is None:
            continue
        c = cov.setdefault(e["type"], {"labelable": 0, "labelled": 0, "by_label": Counter()})
        c["labelable"] += 1
        lab = latest.get(e["event_id"])
        if bound(lab, e):
            c["labelled"] += 1
            c["by_label"][lab["label"]] += 1
    return {k: {**v, "by_label": dict(v["by_label"])} for k, v in cov.items()}


def load_proposals(path: Path | None) -> tuple[bool, str, dict]:
    """(ok, reason, event_id -> its newest proposal row). No file given: no proposals."""
    if path is None:
        return True, "none", {}
    ok, why, rows = chain.verify_file(Path(path))
    if not ok:
        return False, why, {}
    return True, why, {r["event_id"]: r for r in rows if r.get("kind") == "proposal"}


def shown_proposal(ev: dict, props: dict) -> dict | None:
    """The proposal for this event, only when it names the event's V1 row and answers the event's own question."""
    p, q = props.get(ev["event_id"]), question_for(ev["type"])
    ok = p is not None and bound(p, ev) and p.get("question") == q and p.get("label") in QUESTIONS[q]["keys"].values()
    return p if ok else None


def review_split(evs: list[dict], latest: dict) -> dict:
    """type -> {review kind: labels}. Rows before the field existed were made one by one."""
    out: dict = {}
    for e in evs:
        lab = latest.get(e["event_id"])
        if question_for(e["type"]) and bound(lab, e):
            c = out.setdefault(e["type"], dict.fromkeys(REVIEW_KINDS, 0))
            c[lab.get("review") or "one_by_one"] += 1
    return out


def proposal_share(evs: list[dict], latest: dict) -> dict:
    """type -> [labels equal to the proposal they were shown, labels that were shown one]."""
    out: dict = {}
    for e in evs:
        lab = latest.get(e["event_id"])
        if question_for(e["type"]) and bound(lab, e) and lab.get("proposal"):
            c = out.setdefault(e["type"], [0, 0])
            c[0], c[1] = c[0] + bool(lab.get("accepted_proposal")), c[1] + 1
    return out


def parse_roots(specs: list[str]) -> dict:
    return {name: events.resolve(path) for name, path in events.parse_roots(specs).items()}


class Context:
    """The V1 chain and folder behind an event, for the display (read-only, verified once, cached per session)."""

    def __init__(self, roots: dict):
        self.roots, self._chains = roots, {}

    def folder(self, ev: dict) -> Path:
        return self.roots[ev["session"]["root"]] / ev["session"]["id"]

    def chain(self, ev: dict) -> tuple[str, list[dict]]:
        sid = ev["session"]["id"]
        if sid not in self._chains:
            ok, why, rows = chain.verify_file(self.folder(ev) / "session.jsonl")
            self._chains[sid] = ("ok", rows) if ok and rows else (why if not ok else "missing", [])
        return self._chains[sid]

    def index(self, ev: dict) -> int | None:
        """The event's row in its verified chain, or None when the chain is not the one the event names."""
        status, rows = self.chain(ev)
        i = next((n for n, r in enumerate(rows) if r["seq"] == ev["source"]["row_seq"]), None)
        return i if status == "ok" and i is not None and rows[i]["row_sha256"] == ev["source"]["row_sha256"] else None

    def photo(self, ev: dict) -> tuple[Path, str] | None:
        """(the image to show with the event, what it is). A release has no photo of its own: it is offered the last
        judge frame before it, captioned with its age; no image is ever presented as something it is not."""
        ph = ev.get("photo") or {}
        if ph.get("file"):
            what = {"freeze": "the freeze photo" + (", the frame the triggering hand message was computed on"
                                                    if ph.get("is_the_trigger_frame") else ""),
                    "judge_veto": "the frame the judge saw",
                    "window": f"the nearest stored frame, {ph.get('dt_s', 0):+.1f} s from the window's anchor"
                    }.get(ev["type"], "the event's photo")
            return self.folder(ev) / ph["file"], what
        i = self.index(ev) if ev["type"] == "release" else None
        if i is None:
            return None
        rows = self.chain(ev)[1]
        j = next((n for n in range(i - 1, max(-1, i - RELEASE_FRAME_ROWS), -1)
                  if rows[n]["kind"] == "judge" and rows[n].get("frame")), None)
        if j is None:
            return None
        dt = wall_s(rows[i].get("t_iso")) - wall_s(rows[j].get("t_iso"))
        steps = sum(1 for r in rows[j + 1:i] if r["kind"] == "step")
        return self.folder(ev) / rows[j]["frame"], (
            f"no photo at the release; the judge frame of #{rows[j]['seq']} ({rows[j].get('phase')}), {dt:.0f} s "
            f"earlier by wall clock (1 s resolution), {steps} steps between")


    def photo_after(self, ev: dict) -> tuple[Path, str] | None:
        """A release: the first stored frame after it (a freeze photo or a judge frame) within AFTER_S, with its age."""
        i = self.index(ev) if ev["type"] == "release" else None
        if i is None:
            return None
        rows = self.chain(ev)[1]
        t0 = wall_s(rows[i].get("t_iso"))
        for r in rows[i + 1:]:
            if r["kind"] == "step":
                continue
            dt = wall_s(r.get("t_iso")) - t0
            if dt > AFTER_S:
                return None
            f = r.get("frame") or r.get("photo")
            if r["kind"] in ("judge", "freeze") and f:
                return self.folder(ev) / f, (f"the first stored frame after the release: #{r['seq']} {r['kind']}, "
                                             f"+{dt:.0f} s by wall clock (1 s resolution)")
        return None


def wall_s(t_iso: str | None) -> float:
    return datetime.datetime.fromisoformat(t_iso).timestamp() if t_iso else float("nan")


# ------------------------------------------------------------------------------------------------ the display
def fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, list):
        return "[" + ", ".join(fmt(x) for x in v) + "]"
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, separators=(", ", ": "))
    return "-" if v is None else str(v)


def row_line(r: dict) -> str:
    k = r["kind"]
    if k == "judge":
        v = r.get("verdict") or {}
        bits = [str(r.get("phase")), f"{v.get('recommend')} p_unsafe {v.get('p_unsafe')} {v.get('latency_s')} s",
                "UNSAFE" if r.get("unsafe") else "not UNSAFE"]
    else:
        bits = [str(r[x]) for x in ("phase", "monitor", "code", "state", "key", "requested", "why", "detail")
                if r.get(x) not in (None, "")]
        if k == "plan":
            p = r.get("plan") or {}
            bits.append("ok" if p.get("ok") else f"REFUSED: {p.get('reason')}")
    return f"#{r['seq']} {k} " + " · ".join(b[:100] for b in bits)


def head_lines(ev: dict) -> list[str]:
    st = ev.get("step") or {}
    out = [f"━━ {ev['event_id']} · {ev['type']} · {ev['subtype']} · {ev['time']['wall_iso']} · "
           f"{st.get('phase', '-')} · trial {ev.get('trial_id') or '-'}"]
    if ev["type"] == "freeze":
        tr, lat, g = ev["trigger"], ev["latency"], ev["goals"]
        out.append(f"trigger   monitor {tr.get('monitor')} ({tr.get('code')}, {tr.get('source')}): {tr.get('detail')}")
        ms = lat.get("frame_to_hold_ms")
        out.append(f"held      {'yes' if g.get('held') else 'NO'} ({fmt(g.get('hold_attempts'))} attempt(s)) · "
                   + (f"frame→hold {ms:.1f} ms" if ms is not None else f"no frame→hold: {lat.get('why')}"))
    h = ev.get("hands") or {}
    if h:
        out.append(f"hands     {fmt(h.get('n'))} · palms {fmt(h.get('palms_mm'))} mm ({h.get('source')})")
    j = ev.get("judge")
    if j:
        out.append(f"judge     #{j['seq']} {j['phase']} {j['rubric']} {fmt(j['latency_s'])} s: "
                   f"{str(j['recommend']).upper()}, p_unsafe {j['p_unsafe']} "
                   f"({'UNSAFE' if j['unsafe_by_code'] else 'not UNSAFE'} by the code's reading)")
    if ev.get("plan"):
        p = ev["plan"]
        out.append(f"plan      {p['requested']} refused by the {p['refused_by']}: {p['reason']}")
    if ev.get("effect"):
        out.append(f"effect    {ev['effect']['kind']} (#{fmt(ev['effect']['seq'])})")
    if ev["type"] == "window":
        w = ev["window"]
        out.append(f"window    {w['category']}: {w['category_text']} · era {w['era']} · episode {w['episode_steps']} "
                   f"step(s) · hands max {w['hands_max']} · ±3 s")
        out.append("monitor 7 " + "; ".join(f"{k}… ×{v}" for k, v in w["m7_details"].items()))
        out.append("frames    " + ("; ".join(f"#{f['seq']} {f['kind']} {f['dt_s']:+.1f} s" for f in w["frames"])
                                   or f"none within {AFTER_S / 2:g} s"))
    if ev["type"] == "release":
        out.append(f"released  by {ev['released_by']}; verdict {fmt(ev.get('verdict'))}")
        out += [f"{k:<9s} {fmt(ev[k])}" for k in ("palm_mm", "cp3_context", "wait_clear", "outcome_label")
                if ev.get(k)]
    if ev.get("followed_by"):
        out.append("then      " + "; ".join(f"#{d['seq']} {d['kind']}" + (f" [{d['key']}]" if d.get("key") else "")
                                           + (f" by {d['by']}" if d.get("by") else "") for d in ev["followed_by"][:5]))
    return out


def rows_around(rows: list[dict], i: int) -> list[str]:
    before = [r for r in reversed(rows[:i]) if r["kind"] != "step"][:ROWS_BEFORE][::-1]
    after = [r for r in rows[i + 1:] if r["kind"] != "step"][:ROWS_AFTER]
    return ["    " + row_line(r) for r in before] + ["  ▶ " + row_line(rows[i])] + ["    " + row_line(r) for r in after]


def rows_in_span(rows: list[dict], i: int, w: dict) -> list[str]:
    """A window's non-step rows whose time (the last step before them) lies inside its span; the anchor marked."""
    out, last_t = [], None
    for n, r in enumerate(rows):
        if r["kind"] == "step":
            last_t = r["t_s"]
        elif last_t is not None and w["from_s"] <= last_t <= w["to_s"]:
            out.append(("  ▶ " if n == i else "    ") + row_line(r))
    return out or ["    (none)"]


def step_table(rows: list[dict], ev: dict) -> list[str]:
    st = ev.get("step") or {}
    t0, k0 = st.get("t_s"), st.get("k")
    if t0 is None:
        return ["steps ±3 s: none (the event precedes the first step)"]
    out = [f"steps ±3 s, every {STEP_EVERY}rd (Δt from step k={k0}):",
           "      Δt  phase            cmd   m7 hands/present/of5  fired   hands_mm (palms)        tcp mm"]
    for r in rows:
        if r["kind"] != "step" or abs(r["t_s"] - t0) > STEP_WINDOW_S or (r.get("k", 0) - (k0 or 0)) % STEP_EVERY:
            continue
        mon = r.get("monitors") or {}
        m7 = mon.get("7") or {}
        fired = ",".join(f"m{n}" for n, m in sorted(mon.items()) if m.get("fired")) or "-"
        out.append(f"  {r['t_s'] - t0:+6.2f}  {str(r.get('phase')):16s} {'sent' if r.get('target6') else '-':5s} "
                   f"{fmt(m7.get('n_hands'))}/{'yes' if m7.get('present') else 'no'}/{fmt(m7.get('n_of_5')):12s} "
                   f"{fired:7s} {fmt(r.get('hands_mm')):23s} {fmt(r.get('tcp_mm'))}")
    return out


def render(ev: dict, ctx: Context) -> str:
    out = head_lines(ev)
    ph = ctx.photo(ev)
    out.append(f"photo     {ph[0]} ([p] opens it): {ph[1]}" if ph is not None else "photo     none")
    pa = ctx.photo_after(ev)
    if pa is not None:
        out.append(f"after     {pa[0]} ([p] opens it too): {pa[1]}")
    status, rows = ctx.chain(ev)
    i = ctx.index(ev)
    if i is None:
        out.append(f"(no context: the source chain is {status if status != 'ok' else 'not the one this event names'})")
        return "\n".join(out)
    if ev["type"] == "window":
        rows_part = ["rows in the window (steps skipped):"] + rows_in_span(rows, i, ev["window"])
    else:
        rows_part = ["rows around (steps skipped):"] + rows_around(rows, i)
    return "\n".join(out + rows_part + step_table(rows, ev))


# ------------------------------------------------------------------------------------------------ asking
def ask_one(ev: dict, ctx: Context, ask, say, opener, proposal: dict | None = None):
    """(label, note), or "skip" / "quit". With a proposal, Enter takes the proposed label (no note asked: the row
    keeps the proposal's reason); an answer key overrides it and asks for a note."""
    q = QUESTIONS[question_for(ev["type"])]
    say(render(ev, ctx))
    say(q["text"])
    for line in q["help"]:
        say(f"    {line}")
    keys = q["keys"]
    if proposal is not None:
        say(f"  proposed: {proposal['label']} ({proposal.get('confidence')} confidence, by {proposal.get('by')}): "
            f"{proposal.get('reason')}")
    say("  " + "   ".join(f"[{k}] {v}" for k, v in keys.items()) + "   [p] photo   [s] skip   [q] quit"
        + (f"   [Enter] take the proposed {proposal['label']}" if proposal is not None else ""))
    while True:
        a = str(ask("  label: ")).strip().lower()
        if a == "" and proposal is not None:
            return proposal["label"], ""
        if a in keys:
            return keys[a], str(ask("  note (Enter for none): ")).strip()
        if a in ("s", "q"):
            return "skip" if a == "s" else "quit"
        if a == "p":
            shown = [ph for ph in (ctx.photo(ev), ctx.photo_after(ev)) if ph is not None and ph[0].is_file()]
            for ph in shown:
                opener(str(ph[0]))
            if not shown:
                say("  no photo for this event")
            continue
        say(f"  not one of: {', '.join([*keys, 'p', 's', 'q'])}")


def label_row(ev: dict, answer: str, note: str, labeller: str, supersedes: int | None,
              proposal: dict | None = None, review: str = "one_by_one", review_note: str | None = None) -> dict:
    q = question_for(ev["type"])
    row = {"kind": "label", "t_iso": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "event_id": ev["event_id"], "event_type": ev["type"], "event_subtype": ev["subtype"],
           "event_row_sha256": ev["source"]["row_sha256"], "question": q, "question_text": QUESTIONS[q]["text"],
           "label": answer, "note": note, "labeller": labeller, "supersedes": supersedes, "review": review}
    if review_note:
        row["review_note"] = review_note
    if proposal is not None:
        row["proposal"] = {"seq": proposal["seq"], "row_sha256": proposal["row_sha256"], "label": proposal["label"],
                           "confidence": proposal.get("confidence"), "reason": proposal.get("reason"),
                           "by": proposal.get("by")}
        row["accepted_proposal"] = answer == proposal["label"]
    return row


def default_labeller() -> str:
    try:
        name = subprocess.run(["git", "-C", str(HERE), "config", "user.name"], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    return name or "operator"


def open_file(path: str) -> None:
    subprocess.run(["open", path], check=False)               # macOS: the default viewer, on the operator's [p]


def recheck_proposal(lab: dict) -> dict:
    """A bulk or model-panel label shown back to the operator as a proposal: Enter makes it theirs."""
    p = lab.get("proposal") or {}
    return {"label": lab["label"], "confidence": p.get("confidence"),
            "reason": p.get("reason") or f"the current {lab.get('review')} label", "seq": lab["seq"],
            "by": f"label row #{lab['seq']} ({lab.get('review')})", "row_sha256": lab["row_sha256"]}


def queue(evs: list[dict], latest: dict, a: argparse.Namespace, props: dict) -> list[dict]:
    if a.event:
        return [e for e in evs if e["event_id"] == a.event and question_for(e["type"])]
    if a.recheck:
        kinds = set(a.recheck.split(","))
        return [e for e in evs if question_for(e["type"]) and bound(latest.get(e["event_id"]), e)
                and latest[e["event_id"]].get("review") in kinds
                and (not a.session or e["session"]["id"] == a.session) and (not a.type or e["type"] == a.type)]
    levels = None if not a.confidence else {x.strip() for x in a.confidence.split(",")}
    return [e for e in evs if question_for(e["type"]) and not bound(latest.get(e["event_id"]), e)
            and (not a.session or e["session"]["id"] == a.session) and (not a.type or e["type"] == a.type)
            and (levels is None or (shown_proposal(e, props) or {}).get("confidence") in levels)]


def print_coverage(evs: list[dict], latest: dict, say) -> None:
    say(f"coverage ({METHOD_COVERAGE})")
    share, split = proposal_share(evs, latest), review_split(evs, latest)
    for etype, c in coverage(evs, latest).items():
        say(f"  {etype:16s} {c['labelled']}/{c['labelable']} labelled, {c['labelable'] - c['labelled']} unreviewed"
            + (f"  {c['by_label']}" if c["by_label"] else ""))
        if etype in split:
            k = split[etype]
            say(f"  {'':16s} {k['one_by_one']} one by one, {k['bulk']} in bulk (proposals accepted without looking at "
                f"the event), {k['model_panel']} by a model panel")
        if etype in share:
            say(f"  {'':16s} {share[etype][0]} of {share[etype][1]} labelled took the proposal unchanged")
    for eid in unbound(evs, latest):
        say(f"  {eid}: its label names another row (not counted; asked again)")


def accept_in_bulk(todo: list[dict], props: dict, latest: dict, labels: Path, labeller: str, why: str, say,
                   kind: str = "bulk") -> int:
    """Each queued event's proposal written as its label, review `kind` ("bulk": a person accepted proposals unseen;
    "model_panel": the labels are a model panel's); events without a proposal stay unreviewed."""
    done = left = 0
    for ev in todo:
        prop = shown_proposal(ev, props)
        if prop is None:
            left += 1
            continue
        prev = latest.get(ev["event_id"])
        latest[ev["event_id"]] = chain.append_row(labels, label_row(
            ev, prop["label"], "", labeller, None if prev is None else prev["seq"], prop, kind, why))
        done += 1
    say(f"accepted {done} proposals (review \"{kind}\": no person looked at these events); "
        f"{left} without a proposal left unreviewed")
    return 0


def main(argv: list | None = None, ask=input, say=print, opener=open_file) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events-dir", default=str(HERE / "out" / "events"))
    ap.add_argument("--labels", default=str(HERE / "labels" / "labels.jsonl"))
    ap.add_argument("--root", action="append", default=None, help="name=path, as events.py (default: its roots)")
    ap.add_argument("--labeller", default=None, help="default: this repo's git user.name")
    ap.add_argument("--session", default=None)
    ap.add_argument("--type", default=None, choices=sorted(QUESTION_OF_TYPE))
    ap.add_argument("--event", default=None, help="one event id, e.g. KH-S20261005T015041#806")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--proposals", default=None, help="hash-chained proposed labels to show (Enter takes one)")
    ap.add_argument("--confidence", default=None, help="with --proposals: only these, e.g. low or high,medium")
    ap.add_argument("--accept-proposals", action="store_true", help="write the queued proposals as labels, unseen")
    ap.add_argument("--bulk-note", default=None, help="with --accept-proposals: why nobody looked at the events")
    ap.add_argument("--review-kind", default="bulk", choices=REVIEW_KINDS[1:], help="with --accept-proposals")
    ap.add_argument("--recheck", default=None,
                    help="bulk and/or model_panel: the events those labels came from, for the operator one by one")
    a = ap.parse_args(argv)
    if (a.confidence or a.accept_proposals) and not a.proposals:
        say("--confidence and --accept-proposals work on proposals: give --proposals too")
        return 2
    if a.accept_proposals and not a.bulk_note:
        say("--accept-proposals writes labels nobody looked at: give --bulk-note saying why")
        return 2
    if a.recheck and not set(a.recheck.split(",")) <= set(REVIEW_KINDS[1:]):
        say("--recheck takes bulk and/or model_panel (comma separated)")
        return 2
    ok, why, rows = chain.verify_file(Path(a.labels))
    if not ok:
        say(f"the labels file is broken ({why}): nothing is asked or appended")
        return 1
    ok, why, props = load_proposals(None if a.proposals is None else Path(a.proposals))
    if not ok:
        say(f"the proposals file is broken ({why}): nothing is asked or appended")
        return 1
    evs, latest = load_events(Path(a.events_dir)), latest_labels(rows)
    if a.coverage:
        print_coverage(evs, latest, say)
        return 0
    ctx, labeller = Context(parse_roots(a.root or list(events.DEFAULT_ROOTS))), a.labeller or default_labeller()
    todo = queue(evs, latest, a, props)
    if a.event and not todo:
        say(f"{a.event}: no labelable event with that id")
        return 2
    if a.accept_proposals:
        return accept_in_bulk(todo, props, latest, Path(a.labels), labeller, a.bulk_note, say, a.review_kind)
    for n, ev in enumerate(todo, 1):
        say(f"\n[{n}/{len(todo)}]")
        prop = recheck_proposal(latest[ev["event_id"]]) if a.recheck else shown_proposal(ev, props)
        got = ask_one(ev, ctx, ask, say, opener, prop)
        if got == "quit":
            break
        if got == "skip":
            continue
        prev = latest.get(ev["event_id"])
        row = chain.append_row(Path(a.labels), label_row(ev, *got, labeller, None if prev is None else prev["seq"],
                                                          prop))
        latest[ev["event_id"]] = row
        say(f"  saved: label #{row['seq']} for {ev['event_id']} = {row['label']}"
            + (f" (supersedes #{row['supersedes']})" if row["supersedes"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
