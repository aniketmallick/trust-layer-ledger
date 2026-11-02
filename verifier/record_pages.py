"""V2 ledger, M4b: the two pages built from a field record (field_record.py). Static, self-contained, no server.

field_record.html, for a customer, certifier or insurer reading without us in the room, sections in order: 1 the
header, 2 four indicator tiles with sparklines (each with its method on hover and in print), 3 the timeline, 4 hold
latency, 5 stops by source per week, 6 the requirements map, 7 the version comparison (scaffolded), 8 not covered and
how to verify. event.html: any event of the record by id (location.hash): the clip or the stored photo, the steps 3 s
either side, the monitor lanes, the judge call, the label and who made it, the chain hashes; one scrub bar drives the
clip, the lanes and the rows. The look is V1's present.html (its palette and badge).
"""
from __future__ import annotations

import base64
import html
import json
from collections import Counter, defaultdict
from pathlib import Path

import field_record as fr
import label

COL = {"bg": "#07080a", "panel": "#101215", "ink": "#eef0f2", "mut": "#8b9096", "line": "#262a30", "red": "#ff4d4f",
       "green": "#3ddc84", "amber": "#ffb020", "violet": "#b48cff", "teal": "#2ec4b6", "orange": "#ff8a3d",
       "blue": "#4da3ff", "grey": "#8b9096"}
LABEL_COL = {"true_positive": "green", "yes": "green", "false_alarm": "red", "no": "red", "unclear": "amber",
             "correct_no_fire": "green", "miss": "red"}
SOURCE_COL = dict(zip(fr.SOURCES, ("red", "orange", "amber", "grey", "violet", "blue", "teal")))
CSS = """
:root{--bg:#07080a;--panel:#101215;--ink:#eef0f2;--mut:#8b9096;--line:#262a30;--red:#ff4d4f;--green:#3ddc84;
--amber:#ffb020;--violet:#b48cff;--teal:#2ec4b6;--orange:#ff8a3d;--blue:#4da3ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",
Helvetica,Arial,sans-serif}
main{max-width:1180px;margin:0 auto;padding:22px 16px 60px}
h1{font-size:24px;margin:6px 0 4px}h2{font-size:17px;margin:0 0 10px;color:var(--ink)}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:14px 0}
.stmt{display:inline-block;border-left:8px solid var(--amber);background:rgba(8,9,11,.8);padding:8px 14px;
border-radius:8px;font-weight:600}
dl.facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:6px 18px;margin:14px 0 0}
dl.facts div{border-top:1px solid var(--line);padding-top:6px}dt{color:var(--mut);font-size:12px}dd{margin:0}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;word-break:break-all}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.tile{position:relative;background:#0c0e11;border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.t-name{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.t-val{font-size:28px;font-weight:700;margin:2px 0}.t-sub{color:var(--mut);font-size:12px}
.method{display:none;margin-top:8px;color:var(--mut);font-size:11.5px;border-top:1px dashed var(--line);padding-top:6px}
.tile:hover .method{display:block}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:12px}
@media (max-width:520px){.two{grid-template-columns:1fr}}
.ns{color:var(--amber);font-size:13px;font-weight:600}
table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;padding:7px 8px;
border-bottom:1px solid var(--line)}th{color:var(--mut);font-weight:600;font-size:12px}
.legend{color:var(--mut);font-size:12px;margin-top:6px}.legend span{margin-right:14px;white-space:nowrap}
svg text{fill:var(--mut);font-size:11px}a{color:var(--blue)}
pre{background:#0c0e11;border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto;font-size:12px}
@media (max-width:640px){main{padding:14px 12px}.t-val{font-size:24px}}
@media print{body{background:#fff;color:#000}section,.tile{background:#fff;border-color:#bbb}.method{display:block}
svg text{fill:#333}.stmt{background:#fff}}
"""


def esc(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def script_json(obj, id_: str) -> str:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return f'<script type="application/json" id="{id_}">{text}</script>'


def rows_of(rec: dict, kind: str) -> list:
    return [r for r in rec["rows"] if r["kind"] == kind]


def metric(rec: dict, name: str) -> dict | None:
    return next((r for r in rec["rows"] if r["kind"] == "metric" and r["name"] == name), None)


def why_not(rec: dict, name: str) -> str | None:
    return next((r["why"] for r in rec["rows"] if r["kind"] == "not_shown" and r["name"] == name), None)


def short(h) -> str:
    return "-" if not h else str(h)[:12]


def fmt(v, nd: int = 1) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


# ------------------------------------------------------------------------------------------ section 1
def header(rec: dict) -> str:
    srcs, labs = rows_of(rec, "source"), [m for m in rows_of(rec, "metric") if m["name"].startswith("labels/")]
    n_lab = sum(m["value"]["labelable"] for m in labs)
    own = sum(m["value"]["one_by_one"] for m in labs)
    anyl = sum(m["value"]["labelable"] - m["value"]["unreviewed"] for m in labs)
    cov = (f"{100 * own / n_lab:.0f} % by the operator one by one; {100 * anyl / n_lab:.0f} % with bulk and "
           f"model-panel labels ({n_lab} labelable events)") if n_lab else "no labelable event"
    runners = sorted({r["runner_code_sha256"] for r in srcs if r["runner_code_sha256"]})
    g3 = sorted({(r["g3"] or {}).get("version") for r in srcs if r["g3"]} - {None})
    judges = sorted({r["judge_model"] for r in srcs if r["judge_model"]})
    h = rows_of(rec, "header")[0]
    facts = [("Robot", h["robot"]), ("System", h["system"]),
             ("Policy version", f"V1 scripted runner, code {', '.join(short(x) for x in runners) or '-'} "
                                f"(per-file sha256, combined); guard g3 {', '.join(g3) or '-'}; "
                                f"judge {', '.join(judges) or '-'}"),
             ("Window", metric(rec, "armed_hours")["window"]),
             ("Sessions", ", ".join(rec["scope"]["sessions"])),
             ("Armed hours", f"{metric(rec, 'armed_hours')['value']:.3f} h"), ("Label coverage", cov),
             ("Operator override", f"{metric(rec, 'override_sessions')['value']} of {len(srcs)} sessions"),
             ("Record chain head", f"#{rec['head']['seq']} {rec['head']['row_sha256']}")]
    dl = "".join(f'<div><dt>{esc(k)}</dt><dd class="{"mono" if k == "Record chain head" else ""}">{esc(v)}</dd></div>'
                 for k, v in facts)
    selected = "".join(f'<div class="stmt">{esc(r["statement"])}</div>' for r in rows_of(rec, "scope"))
    return (f'<section data-section="Header"><div class="stmt">{esc(h["statement"])}</div>{selected}'
            f'<h1>Field record: SO-101 trust layer</h1><dl class="facts">{dl}</dl></section>')


# ------------------------------------------------------------------------------------------ section 2
def sparkline(values: list, colour: str = "blue") -> str:
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    w, h = 200, 36
    if not pts:
        return f'<svg width="{w}" height="{h}" role="img" aria-label="no data"></svg>'
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    n = max(1, len(values) - 1)
    xy = [(4 + (w - 8) * i / n, h - 4 - (h - 8) * ((v - lo) / (hi - lo) if hi > lo else 0.5)) for i, v in pts]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="{COL[colour]}"/>' for x, y in xy)
    return (f'<svg width="{w}" height="{h}" role="img" aria-label="per session">'
            f'<polyline points="{line}" fill="none" stroke="{COL[colour]}" stroke-width="1.6"/>{dots}</svg>')


def per_session(rec: dict) -> dict:
    srcs = rows_of(rec, "source")
    ev = defaultdict(list)
    for r in rows_of(rec, "event"):
        ev[r["session_id"]].append(r)
    out = {"freezes": [], "fa": [], "p95": []}
    for s in srcs:
        rows = ev[s["session_id"]]
        out["freezes"].append(sum(r["type"] == "freeze" for r in rows))
        fa = sum(r["type"] == "freeze" and r["review"] == "one_by_one" and r["label"] == "false_alarm" for r in rows)
        out["fa"].append(round(100 * fa / (s["armed_s"] / 3600), 1) if s["armed_s"] > 0 else None)
        lats = [r["frame_to_hold_ms"] for r in rows if r["frame_to_hold_ms"] is not None]
        out["p95"].append(fr.nearest_rank(lats, 95))
    return out


def tile(name: str, value: str, sub: str, spark: str, method: str) -> str:
    return (f'<div class="tile" title="{esc(method)}"><div class="t-name">{esc(name)}</div>'
            f'<div class="t-val">{value}</div>{spark}<div class="t-sub">{sub}</div>'
            f'<div class="method">{esc(method)}</div></div><!--/tile-->')


def tiles(rec: dict) -> str:
    ps = per_session(rec)
    fz = metric(rec, "events_by_type/freeze")
    fa = metric(rec, "false_alarms_per_100h/freeze")
    miss, panel = metric(rec, "misses_found_in_review"), metric(rec, "model_panel_window_review")
    lat = metric(rec, "frame_to_hold_ms")
    t1 = tile("Freezes", esc(fz["value"] if fz else 0), f"per session: {esc(ps['freezes'])}",
              sparkline(ps["freezes"], "red"), (fz or metric(rec, "stops_by_source"))["method"])
    if fa:
        t2 = tile("False alarms per 100 h (freezes)", esc(fmt(fa["value"])),
                  esc(f"{fa['count']} operator-labelled false alarms in {fa['denominator']['value']:.3f} armed h; "
                      f"label coverage {100 * fa['coverage']['human']:.0f} %"), sparkline(ps["fa"], "amber"),
                  fa["method"])
    else:
        why = why_not(rec, "false_alarms_per_100h/freeze") or "no freeze in the scope"
        t2 = tile("False alarms per 100 h (freezes)", '<span class="ns">not shown</span>', esc(why), sparkline([]),
                  fr.M_FA)
    psub = ("" if not panel else f"; the model panel read {panel['count']} windows: {panel['value']['miss']} miss, "
            f"{panel['value']['correct_no_fire']} no stop needed, {panel['value']['unclear']} unclear "
            f"(not ground truth)")
    if miss:
        t3 = tile("Misses found in review", esc(miss["value"]),
                  esc(f"in {miss['denominator']['value']} windows reviewed by the operator{psub}"), sparkline([]),
                  miss["method"])
    else:
        t3 = tile("Misses found in review", '<span class="ns">not shown</span>',
                  esc((why_not(rec, "misses_found_in_review") or "") + psub), sparkline([]), fr.M_MISS)
    v = lat["value"]
    t4 = tile("Hold latency p95", esc(f"{fmt(v['p95'])} ms") if v["n"] else '<span class="ns">no data</span>',
              esc(f"n {v['n']}; p50 {fmt(v['p50'])} ms, max {fmt(v['max'])} ms"), sparkline(ps["p95"], "teal"),
              lat["method"])
    return f'<section data-section="Indicators"><h2>Indicators</h2><div class="tiles">{t1}{t2}{t3}{t4}</div></section>'


# ------------------------------------------------------------------------------------------ section 3
def mark_colour(r: dict) -> str:
    if r["type"] in ("operator_hold", "override_stamp"):
        return COL["violet"] if r["type"] == "operator_hold" else COL["teal"]
    return COL[LABEL_COL.get(r["label"], "grey")] if r["label"] else COL["grey"]


def timeline(rec: dict) -> str:
    srcs = rows_of(rec, "source")
    ev = defaultdict(list)
    for r in rows_of(rec, "event"):
        ev[r["session_id"]].append(r)
    misses = [w for w in rows_of(rec, "window") if w["label"] == "miss"]
    span = max([s["armed_s"] for s in srcs] + [1.0])
    lw, bw, rh = 230, 860, 30
    rows_svg = []
    for i, s in enumerate(srcs):
        y = 18 + i * rh
        x_of = lambda t: lw + bw * max(0.0, min(t, s["armed_s"])) / span  # noqa: E731
        marks = []
        for r in ev[s["session_id"]]:
            t = r["rel_s"] if r["rel_s"] is not None else 0.0
            c = mark_colour(r)
            own = r["review"] in (None, "one_by_one")
            shape = (f'<circle cx="{x_of(t):.1f}" cy="{y}" r="5.5" fill="{c}" fill-opacity="{1 if own else 0.25}" '
                     f'stroke="{c}" stroke-width="{0 if own else 1.6}"')
            tip = (f"{r['event_id']} {r['type']} {r['subtype']} - {r['label'] or 'unreviewed'}"
                   + ("" if own else f" ({r['review']})"))
            marks.append(f'<a href="event.html#{esc(r["event_id"])}">{shape}><title>{esc(tip)}</title></circle></a>')
        for w in misses:
            if w["session_id"] == s["session_id"]:
                xm = x_of(w["rel_s"] or 0)
                marks.append(f'<rect x="{xm - 5:.1f}" y="{y - 5}" width="10" height="10" fill="none" '
                             f'stroke="{COL["red"]}" transform="rotate(45 {xm:.1f} {y})">'
                             f'<title>{esc(w["window_id"])} miss</title></rect>')
        rows_svg.append(f'<g class="tl-row"><text x="0" y="{y + 4}">{esc(s["session_id"])} '
                        f'({s["armed_s"] / 60:.1f} min)</text><rect x="{lw}" y="{y - 3}" '
                        f'width="{bw * s["armed_s"] / span:.1f}" height="6" rx="3" fill="#2a3038"/>'
                        f'{"".join(marks)}</g>')
    h = 30 + len(srcs) * rh
    dot = '<span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="{}"/></svg> {}</span>'
    legend = ("".join(dot.format(COL[c], t) for t, c in (("true positive / released into a ready hand", "green"),
                                   ("false alarm / not into a ready hand", "red"), ("unclear", "amber"),
                                   ("unreviewed", "grey"), ("operator hold", "violet"), ("override stamp", "teal")))
              + "<span>filled: the operator's own label; faint ring: bulk or model-panel label</span>"
              + f"<span>hollow diamond: a miss found in review ({len(misses)} in this scope)</span>")
    return (f'<section data-section="Timeline"><h2>Timeline</h2><svg viewBox="0 0 {lw + bw + 10} {h}" width="100%" '
            f'role="img" aria-label="sessions and their events">{"".join(rows_svg)}</svg>'
            f'<div class="legend">{legend}</div><div class="legend">Each bar is the session\'s armed time (control '
            f'loop span, common scale); click a mark for the event.</div></section>')


# ------------------------------------------------------------------------------------------ section 4
def latency(rec: dict) -> str:
    m = metric(rec, "frame_to_hold_ms")
    series = metric(rec, "frame_to_hold_ms_series")["value"]
    v = m["value"]
    if not v["n"]:
        body = "<p>No monitor-7 freeze with a frame_to_hold_ms in this scope.</p>"
    else:
        vals = [x[2] for x in series]
        top = max(10, 10 * (int(max(vals)) // 10 + 1))
        bins = Counter(int(x // 10) for x in vals)
        w, h, pad = 520, 170, 30
        bw = (w - 2 * pad) / (top / 10)
        hmax = max(bins.values())
        bars = "".join(f'<rect x="{pad + k * bw + 1:.1f}" y="{h - pad - (h - 2 * pad) * c / hmax:.1f}" '
                       f'width="{bw - 2:.1f}" height="{(h - 2 * pad) * c / hmax:.1f}" fill="{COL["blue"]}">'
                       f'<title>{k * 10}-{k * 10 + 10} ms: {c}</title></rect>' for k, c in sorted(bins.items()))
        x = lambda ms: pad + (w - 2 * pad) * ms / top  # noqa: E731
        lines = "".join(f'<line x1="{x(q):.1f}" x2="{x(q):.1f}" y1="{pad - 8}" y2="{h - pad}" stroke="{COL[c]}" '
                        f'stroke-dasharray="4 3"/><text x="{x(q) + dx:.1f}" y="{y}" text-anchor="{anchor}">'
                        f'{t} {q:g} ms</text>'
                        for q, t, c, dx, y, anchor in ((v["p50"], "p50", "green", -4, pad - 12, "end"),
                                                       (v["p95"], "p95", "amber", 4, pad - 2, "start")))
        axis = "".join(f'<text x="{x(t):.1f}" y="{h - pad + 14}" text-anchor="middle">{t}</text>'
                       for t in range(0, top + 1, max(10, top // 6 // 10 * 10)))
        hist = (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="histogram">'
                f'<line x1="{pad}" x2="{w - pad}" y1="{h - pad}" y2="{h - pad}" stroke="#2a3038"/>{bars}{lines}{axis}'
                f'<text x="{w - pad}" y="{h - 6}" text-anchor="end">frame to hold, ms (n {v["n"]})</text></svg>')
        n = len(vals)
        dx = lambda i: pad + (w - 2 * pad) * (i / max(1, n - 1))  # noqa: E731
        dy = lambda ms: h - pad - (h - 2 * pad) * ms / top  # noqa: E731
        pts = "".join(f'<circle cx="{dx(i):.1f}" cy="{dy(ms):.1f}" r="3" fill="{COL["teal"]}"><title>{esc(sid)} '
                      f'+{rel:.0f} s: {ms:g} ms</title></circle>' for i, (sid, rel, ms) in enumerate(series))
        drift = (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="latency over time">'
                 f'<line x1="{pad}" x2="{w - pad}" y1="{dy(v["p95"]):.1f}" y2="{dy(v["p95"]):.1f}" '
                 f'stroke="{COL["amber"]}" stroke-dasharray="4 3"/>{pts}'
                 f'<text x="{pad}" y="{h - 6}">freezes in time order (sessions in the order of the record)</text>'
                 f'</svg>')
        body = f'<div class="two"><div>{hist}</div><div>{drift}</div></div>'
    return (f'<section data-section="Hold latency"><h2>Hold latency</h2>{body}'
            f'<div class="legend">{esc(m["method"])}</div></section>')


# ------------------------------------------------------------------------------------------ section 5
def stops(rec: dict) -> str:
    weeks = metric(rec, "stops_by_source_per_week")["value"]
    w, h, pad = 560, 200, 34
    tot = max([sum(c.values()) for c in weeks.values()] + [1])
    bw = min(90, (w - 2 * pad) / max(1, len(weeks)) - 16)
    bars = []
    for i, (wk, c) in enumerate(weeks.items()):
        x, y = pad + i * (bw + 16), h - pad
        for src in fr.SOURCES:
            n = c.get(src, 0)
            if n:
                hh = (h - 2 * pad) * n / tot
                y -= hh
                bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{hh:.1f}" '
                            f'fill="{COL[SOURCE_COL[src]]}"><title>{esc(wk)} {esc(src)}: {n}</title></rect>')
        bars.append(f'<text x="{x + bw / 2:.1f}" y="{h - pad + 14}" text-anchor="middle">{esc(wk)} '
                    f'({sum(c.values())})</text>')
    legend = "".join(f'<span><svg width="12" height="12"><rect width="12" height="12" fill="{COL[SOURCE_COL[s]]}"/>'
                     f'</svg> {esc(s)}</span>' for s in fr.SOURCES)
    m = metric(rec, "stops_by_source")
    return (f'<section data-section="Stops by source"><h2>Stops by source, per week</h2>'
            f'<svg viewBox="0 0 {w} {h}" width="{w}" style="max-width:100%" role="img" '
            f'aria-label="stops by source">{"".join(bars)}</svg>'
            f'<div class="legend">{legend}</div><div class="legend">{esc(m["method"])}</div></section>')


# ------------------------------------------------------------------------------------------ sections 6-8
def requirements(rec: dict) -> str:
    trs = "".join(f'<tr><td><b>{esc(r["ref"])}</b><br>{esc(r["title"])}</td><td>{esc(r["shown"])}</td>'
                  f'<td>{esc(r["not_shown"])}</td></tr>' for r in rows_of(rec, "requirement"))
    return (f'<section data-section="Requirements map"><h2>Requirements map</h2><table><tr><th>Requirement</th>'
            f'<th>Shown by this record</th><th>Not shown</th></tr>{trs}</table><div class="legend">The record shows '
            f'evidence for a reader to weigh against each requirement; it marks none of them as met.</div></section>')


def versions(rec: dict) -> str:
    srcs = rows_of(rec, "source")
    runners = ", ".join(sorted({short(r["runner_code_sha256"]) for r in srcs}))

    def val(name, key=None, nd=1):
        m = metric(rec, name)
        if m is None:
            return "not shown" if why_not(rec, name) else "0" if name.startswith("events_by_type/") else "-"
        return fmt(m["value"][key], nd) if key else fmt(m["value"], nd)
    rows = [("Freezes", val("events_by_type/freeze")), ("False alarms per 100 h (freezes)",
                                                        val("false_alarms_per_100h/freeze")),
            ("Judge vetoes", val("events_by_type/judge_veto")),
            ("Planner refusals", val("events_by_type/planner_refusal")),
            ("Misses found in review", val("misses_found_in_review")),
            ("Hold latency p95, ms", val("frame_to_hold_ms", "p95")), ("Armed hours", val("armed_hours", nd=3))]
    trs = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td><td>-</td></tr>" for k, v in rows)
    return (f'<section data-section="Version comparison"><h2>Version comparison</h2><table><tr><th>Metric</th>'
            f'<th>This record (runner {esc(runners)})</th><th>Next policy version</th></tr>{trs}</table>'
            f'<div class="legend">The second column is empty until a second policy version has sessions; the week-8 '
            f'gate fills it with the same rows and methods.</div></section>')


def not_covered(rec: dict) -> str:
    items = "".join(f"<li>{esc(x)}</li>" for x in rows_of(rec, "not_covered")[0]["items"])
    n_src = len(rows_of(rec, "source"))
    lab = rows_of(rec, "labels")[0]
    expect = (f"record: chain ok ({len(rec['rows'])} rows), head {rec['head']['row_sha256'][:16]}\n"
              f"sources: {n_src} of {n_src} chains verify; heads match\n"
              f"labels: chain ok; row {lab['head_seq']} matches the recorded head (... later rows not used)\n"
              f"rebuilt from the sources: identical ({len(rec['rows'])} rows)\nRESULT: VERIFIED")
    return (f'<section data-section="Not covered"><h2>Not covered</h2><ul>{items}</ul><h2>How to verify</h2>'
            f'<p>From clones of v2_ledger and trust-layer-demo side by side (the local sessions are on the rig\'s '
            f'machine only):</p><pre>python3 verify_record.py field_record.json</pre><p>Expected, exit 0:</p>'
            f'<pre>{esc(expect)}</pre></section>')


def record_html(rec: dict) -> str:
    body = "".join(f(rec) for f in (header, tiles, timeline, latency, stops, requirements, versions, not_covered))
    sids = rec["scope"]["sessions"]
    title = f"Field record {rec['scope']['kind']} {sids[0] if len(sids) == 1 else ''}"
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{esc(title.strip())}</title><style>{CSS}</style>'
            f'</head><body><main>{body}</main>{script_json(rec, "record")}</body></html>')


# ------------------------------------------------------------------------------------------ event.html
def data_uri(p: Path) -> str | None:
    try:
        return "data:image/jpeg;base64," + base64.b64encode(Path(p).read_bytes()).decode("ascii")
    except OSError:
        return None


def media(ctx: label.Context, e: dict) -> dict:
    clip = e.get("clip")
    if clip and clip.get("verified"):
        folder = ctx.folder(e)
        try:
            meta = json.loads((folder / clip["json"]).read_text(encoding="utf-8"))
            data = (folder / clip["mjpeg"]).read_bytes()
            frames = [{"dt": f["dt_s"], "src": "data:image/jpeg;base64," + base64.b64encode(
                data[f["offset"]:f["offset"] + f["length"]]).decode("ascii"), "caption": f"frame {f['seq']}"}
                for f in meta["frames"][::2]]
            return {"kind": "clip", "frames": frames, "note": "the +-3 s clip saved with --v2-events (every 2nd frame)"}
        except (OSError, ValueError, KeyError):
            pass
    stills = [(p, cap) for p, cap in (ctx.photo(e) or (None, None), ctx.photo_after(e) or (None, None)) if p]
    frames = [{"dt": None, "src": uri, "caption": cap} for p, cap in stills if (uri := data_uri(p))]
    if frames:
        return {"kind": "photo", "frames": frames, "note": "stored stills (no clip: the session predates M3)"}
    return {"kind": "none", "frames": [], "note": "no stored frame for this event"}


def event_data(rec: dict, scope, ctx: label.Context, row: dict, e: dict) -> dict:
    s = next(x for x in scope.sessions if x.sid == row["session_id"])
    i = next(n for n, r in enumerate(s.rows) if r["seq"] == row["source_row_seq"])
    t0 = (e.get("step") or {}).get("t_s")
    steps, others = [], []
    if t0 is not None:
        last = None
        for r in s.rows:
            if r["kind"] == "step":
                last = r["t_s"]
                if abs(r["t_s"] - t0) <= 3.0:
                    mon = r.get("monitors") or {}
                    steps.append({"dt": round(r["t_s"] - t0, 3), "phase": r.get("phase"),
                                  "cmd": r.get("target6") is not None,
                                  "fired": sorted(int(k) for k, m in mon.items() if m.get("fired")),
                                  "n_hands": (mon.get("7") or {}).get("n_hands"), "palms": r.get("hands_mm"),
                                  "tcp": r.get("tcp_mm")})
            elif last is not None and abs(last - t0) <= 3.0:
                others.append({"dt": round(last - t0, 3), "seq": r["seq"], "text": label.row_line(r),
                               "this": r["seq"] == row["source_row_seq"]})
    lab = fr.bound_label(scope, e)
    jr = None
    jseq = (e.get("judge") or {}).get("seq") if e["type"] in ("freeze", "judge_veto") else None
    if jseq is not None:
        j = next((r for r in s.rows if r["seq"] == jseq), {})
        v = j.get("verdict") or {}
        jr = {"seq": jseq, "phase": j.get("phase"), "recommend": v.get("recommend"), "p_unsafe": v.get("p_unsafe"),
              "latency_s": v.get("latency_s"), "unsafe_by_code": bool(j.get("unsafe")), "reason": v.get("reason")}
    detail = ((e.get("trigger") or {}).get("detail") or (e.get("plan") or {}).get("reason")
              or e.get("released_by") or ((e.get("key") or {}).get("key") and f"[{e['key']['key']}] at the menu")
              or e.get("stamp") or "")
    return {"id": row["event_id"], "type": row["type"], "subtype": row["subtype"], "session": row["session_id"],
            "wall_iso": row["wall_iso"], "rel_s": row["rel_s"], "detail": str(detail),
            "latency_ms": row["frame_to_hold_ms"],
            "label": None if lab is None else {"label": lab["label"], "review": lab.get("review") or "one_by_one",
                                               "note": lab.get("note"), "labeller": lab.get("labeller"),
                                               "seq": lab["seq"], "row_sha256": lab["row_sha256"],
                                               "proposal_by": (lab.get("proposal") or {}).get("by")},
            "judge": jr, "chain": {"seq": row["source_row_seq"], "prev_sha256": s.rows[i]["prev_sha256"],
                                   "row_sha256": s.rows[i]["row_sha256"],
                                   "next_sha256": s.rows[i + 1]["row_sha256"] if i + 1 < len(s.rows) else None},
            "steps": steps, "rows": others, "media": media(ctx, e)}


EVENT_JS = Path(__file__).resolve().parent / "pages" / "event_viewer.js"   # the viewer's script, inlined

EVENT_CSS = CSS + """
.wrap{display:grid;grid-template-columns:260px 1fr;gap:14px}@media (max-width:820px){.wrap{grid-template-columns:1fr}}
#list{max-height:80vh;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:6px}
.li{display:block;padding:5px 8px;border-radius:6px;color:var(--ink);text-decoration:none;font-size:12.5px}
.li.on,.li:hover{background:#1b1f25}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
.mut{color:var(--mut)}#badge{border-left:10px solid #8b9096;background:rgba(8,9,11,.84);padding:10px 16px;
border-radius:10px}#btitle{font-size:20px;font-weight:700}#bsub{color:var(--mut)}
#img img{max-width:100%;border-radius:8px;display:block;margin-top:8px}
#rows tr.at td{background:#1d2a3a}#rows tr.this td{color:var(--amber)}#rows td{font-size:12px;padding:3px 6px}
input[type=range]{width:100%}
"""


def event_html(rec: dict, scope) -> str:
    ctx = label.Context({s.root: s.folder.parent for s in scope.sessions})
    by_id = {e["event_id"]: e for e in scope.events}
    data = [event_data(rec, scope, ctx, r, by_id[r["event_id"]]) for r in rows_of(rec, "event")]
    body = ('<main><div class="stmt">Evidence, not certification. Each event as the V1 chain recorded it.</div>'
            '<h1>Event viewer</h1><div class="wrap"><nav id="list"></nav><div>'
            '<section id="badge"><div id="btitle"></div><div id="bsub"></div></section>'
            '<section><div><input id="scrub" type="range" min="-3" max="3" step="0.0667" value="0" '
            'aria-label="time from the event, seconds"> <span id="tval" class="mut"></span></div>'
            '<div id="lanes"></div></section>'
            '<section><div id="mnote" class="mut"></div><div id="img"></div></section>'
            '<section><dl class="facts" id="facts"></dl></section>'
            '<section><h2>Rows, 3 s either side</h2><table><tr><th>Δt s</th><th>phase / row</th><th>command</th>'
            '<th>fired</th><th>hands</th><th>palms mm</th></tr><tbody id="rows"></tbody></table></section>'
            '</div></div></main>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>Event viewer</title><style>{EVENT_CSS}</style>'
            f'</head><body>{body}{script_json(data, "events")}<script>{EVENT_JS.read_text(encoding="utf-8")}'
            f'</script></body></html>')
