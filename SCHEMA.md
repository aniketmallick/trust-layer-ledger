Evidence, not certification. This file describes the formats; it makes no claim about the system they record.

# Schema

Everything here is JSON. Logs are one object per line (`.jsonl`). A field record is one JSON file with its rows in an
array. The names below are the ones in the files; `verifier/` is the reference for anything this page leaves out.

| Format | Identifier | Where |
|---|---|---|
| Field record | `v2-ledger/field-record/0.1` | `records/**/field_record.json` |
| Event | `v2-ledger/event/0.2` | derived by `verifier/events.py`, projected into `event` rows |
| Review window | `v2-ledger/window/0.1` | projected into `window` rows |
| Left-out manifest | `v2-ledger/left-out/0.1` | `runs/m1_left_out.json` |
| Label | `kind: label` | `labels/labels.jsonl` |
| Session log | written by the runner | `sources/**/session.jsonl` |
| Clip index and clip metadata | `v2-clip/0.1` | `sources/**/v2_clips/` |
| Policy section | `toceta-policy-section/0.1` | top-level key of a policy record |
| Twin section | `toceta-twin-section/0.1` | top-level key of 9 policy records |
| Runner ledger | `toceta-ledger/0.3` | `sources/**/ledger.jsonl`, see [schema/toceta-ledger-0.3.md](schema/toceta-ledger-0.3.md) |

## The hash chain

Session logs, the labels file, the clip index and the rows of a field record all follow one rule, in `verifier/chain.py`:

- row `n` has `seq` equal to `n`, counting from 1;
- its `prev_sha256` is the previous row's `row_sha256`, or `null` for the first row;
- its `row_sha256` is the SHA-256 of the row without that field, serialised as JSON with sorted keys and no spaces.

```python
body = {k: v for k, v in row.items() if k != "row_sha256"}
hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
```

Editing a row changes its hash and breaks the link from the next row. Deleting or reordering rows breaks `seq`. A
missing file reads as an empty chain, so the chain rule alone cannot notice a whole log going missing. The record's
`source` rows are what catch that.

## Field record

| Key | Meaning |
|---|---|
| `schema` | `v2-ledger/field-record/0.1` |
| `scope` | `kind` (`session` or `window`), `sessions` (ids), `window` (`days`, `from`, `to`, or null) |
| `built` | when and by which tool: `iso`, `tool`, `v2_commit`, `events_schema`. Not hashed. |
| `rows` | the record, hash-chained |
| `head` | `seq` and `row_sha256` of the last row |
| `policy`, `twin` | policy records only; see below |

### Row kinds

Every row also has `seq`, `prev_sha256` and `row_sha256`.

| `kind` | Count | Fields |
|---|---|---|
| `header` | 1 | `statement`, `robot`, `system`, `scope`, `sessions`, `window`, `events_schema` |
| `scope` | 1 in a window record | `statement`, `days`, `sessions_on_those_days`, `in_record` (`sessions`, `armed_hours`, `freezes`), `left_out` (the same, plus `chains_verified`), `chosen_by`, `manifest`, `manifest_sha256`, `method` |
| `source` | one per session | `session_id`, `root`, `chain`, `chain_head_seq`, `chain_head_sha256`, `rows`, `started_iso`, `ended_iso`, `mode`, `dry_run`, `operator_override`, `layer_off`, `armed_s`, `hand_feed`, `runner_code_sha256`, `runner_files`, `g3`, `judge_model`, `rubric_sha256`, `registration_sha256`, `clips` (`attached`, `verified`, `problem`) |
| `labels` | 1 | `chain`, `head_seq`, `head_sha256`, `method` |
| `event` | one per event | `event_id`, `type`, `subtype`, `session_id`, `source_row_seq`, `source_row_sha256`, `step_t_s`, `rel_s`, `wall_iso`, `label`, `review`, `label_row_seq`, `label_row_sha256`, `frame_to_hold_ms`, `photo`, `clip` (`mjpeg`, `mjpeg_sha256`, `verified`) |
| `window` | one per sampled window | `window_id`, `category`, `era`, `session_id`, `anchor_row_seq`, `anchor_row_sha256`, `rel_s`, `label`, `review`, `label_row_seq`, `label_row_sha256`, `sampling` |
| `metric` | one per number | `name`, `value`, `unit`, `window`, `denominator` (`what`, `value`), `count`, `coverage`, `method` |
| `not_shown` | one per withheld number | `name`, `why` |
| `not_covered` | 1 | `items`: what the record does not cover, in words |
| `requirement` | 6 | `ref`, `title`, `shown`, `not_shown`: a map onto regulations and standards, not a claim of meeting them |

`photo` and `clip.mjpeg` are paths inside the original session folder. Those files are not published here.

A `metric` always carries its `method` as a sentence. A rate appears only when the operator's own labels cover at least
half of the events behind it. Otherwise there is a `not_shown` row, and the `why` gives the coverage.

### What a window leaves out

A window record covers the sessions chosen for it, so it has to say what else those days held. The `scope` row comes right
after the header, and a window record without one does not verify. Session records do not have one, which is why this
did not change the record's schema version.

The left-out logs are not published, so the row cannot be rebuilt from them the way other rows are. It is rebuilt from
`runs/m1_left_out.json` instead. That file lists each left-out session, one object each: `session_id`, `chain_ok`,
`chain_problem`, `chain_head_seq`, `chain_head_sha256`, `armed_s`, `freezes`. `manifest_sha256` is the SHA-256 of its bytes.
The verifier recomputes the totals and the hash from the file. It cannot check any one session's figures. The heads do
commit to those logs: if one is ever published, its head has to match.

### Events

One event per source row. `event_id` is `<session>#<row seq>`.

| `type` | Source row | `subtype` |
|---|---|---|
| `freeze` | `freeze` | `m7_hand_in_workspace`, `m7_gate`, `m8_hand_link`, `m1`..`m5` by code, `m6_kill_switch`, `m0_<code>` |
| `judge_veto` | `judge` with `unsafe` true | `CP1`, `CP2`, `CP3`, `FREEZE`, `recheck_at_c` |
| `planner_refusal` | `plan` with `ok` false | `<requested>_<planner or code>` |
| `operator_hold` | an accepted `[w]`, `[l]` or `[k]` at a menu | what was chosen, such as `wait` or `kill_torque_off` |
| `release` | `release_auto`, `release_typed`, or `[r]` at the release menu | `auto_cp3`, `typed_release_menu`, `typed_freeze_menu` |
| `override_stamp` | `override`, or a layer-off `session_start` | `operator_override`, `layer_off` |

`freeze`, `judge_veto`, `planner_refusal` and `release` can be labelled. The full event, with its inputs, is rebuilt by
`verifier/events.py` from the session log and is not stored in the record.

### Windows

The miss review samples stretches where the layer did not fire, by category and with a fixed seed (20261015). `sampling`
gives the category, population, sample size, rule and seed. `verifier/review_misses.py` is the sampler.

## Labels

`labels/labels.jsonl` is a chain of `kind: label` rows.

| Field | Meaning |
|---|---|
| `event_id` | the event or window labelled |
| `event_type`, `event_subtype` | as in the event |
| `event_row_sha256` | the hash of the source row. A label counts only for that exact row. |
| `question`, `question_text` | what was asked |
| `label` | `true_positive`, `false_alarm`, `unclear`; `yes`, `no`, `unclear` for releases; `miss`, `correct_no_fire`, `unclear` for windows |
| `note` | free text |
| `labeller` | who answered |
| `supersedes` | the `seq` of the row this corrects, or null. The newest label for an event wins. |
| `review` | `one_by_one`, `bulk` or `model_panel`. Absent means `one_by_one`. |
| `t_iso` | when |

`one_by_one` is the operator answering at a prompt, and it is the only kind counted as ground truth. `bulk` is proposals
accepted without looking at each one. `model_panel` is three model reviewers and an orchestrator.

`labels/policy_labels.jsonl` is empty: no policy event has been labelled.

## Session logs

`session.jsonl` is written by the runner while the arm is on. Rows have a `kind`. The common ones are `session_start`,
`check`, `step`, `freeze`, `judge`, `plan`, `key`, `kill` and `session_end`. A `step` row is one control tick (about
15 Hz) and holds the joint readings `q6`, the command `target6`, the fingertip `tcp_mm`, the gripper `load`, the eight
monitors' states, `phase`, `t_s` and `dt_ms`.

Policy sessions add `run_id`, `source`, `policy_hash`, `policy_name`, `runner_hash`, `t_wall`, `t_mono` and `stamped_by`
to every row.

These rows have never been edited, including where they turned out to be wrong. Corrections live in errata, in
[trust-layer-demo](https://github.com/aniketmallick/trust-layer-demo)'s STATUS and README. That is why the knife
sessions' own labels say "prop" about a tool that was a real screwdriver.

## Clips

`v2_clips/index.jsonl` is a chain with one row per clip, naming the `.mjpeg` and `.json` files and their SHA-256 hashes.
The `.json` (`v2-clip/0.1`) lists the frames: `dt_s`, `offset`, `length`, the frame's hash, and the message the hand
monitor published for it, which holds keypoints and table coordinates and no pixels. The `.mjpeg` files are not
published.

## Sections beside the rows

`policy` and `twin` are top-level keys of a policy record, not rows, because the verifier rebuilds every row from the
logs and would refuse one it could not rebuild.

Both carry `sha256`, the SHA-256 of the section without that key, as JSON with sorted keys, no spaces and
`ensure_ascii=False`. Both carry `binds.record_head`, which must equal the record's `head`.

| `policy` key | Meaning |
|---|---|
| `binds` | `record_schema`, `record_head`, `session`, `session_head` |
| `policy` | `name`, `repo_id`, `revision`, `sha256` and how it was computed, `type`, `chunk_size`, `replan_steps`, `prefetch_steps`, `device`, `task` |
| `training` | from the model's `train_config.json`: `steps`, `batch_size`, `seed`, `job_name`, `wandb_run`, `train_config_sha256` |
| `dataset` | `id`, `revision`, `sha256`, `method` |
| `monitors` | `config`, `config_sha256`, `session_table` (which monitors were off), `stamp`, and the config's own `text` |
| `judge` | a stamp if the judge was off, else null |
| `hand_rule` | a stamp if a distance rule replaced V1's box rule, else null |
| `numbers` | hold timings and their intervals, and the ISO 13855 sum |
| `sentences` | the limits of that session's numbers, in words |
| `runner_hash`, `method`, `schema` | identity and how the section was made |

`twin` links a session's holds to reproductions in simulation: `chain` (the file and head of the reproduction chain,
which is not published), `holds`, `sentences`, `method`.
