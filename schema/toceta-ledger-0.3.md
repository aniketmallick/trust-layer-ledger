# Ledger schema 0.3 (`toceta-ledger/0.3`)

**Evidence, not certification.** This schema adds to V2's event schema (`v2-ledger/event/0.2`); none of V2's fields or
hashes change. It is written by `ledger/events_ext.py` with V2's chain rule, using V2's own `chain.py`, imported and
never edited.

## Where the rows are

| File | Rows | Written by |
|---|---|---|
| `<session>/session.jsonl` | V1's rows, as V1 writes them, plus on every row `run_id`, `source`, `policy_hash`, `policy_name`, `runner_hash` (M2) and `t_wall`, `t_mono`, `stamped_by` (0.3) | V1's `StepLog`, subclassed by the runner (`stamped()`) |
| `<session>/ledger.jsonl` | `identity`, `clock_sync`, `intervention`, `reset` | `events_ext.Ledger`, V2's `chain.append_row` |

The second file is a chain of its own beside the session's, as V1's `v2_clips/index.jsonl` is. Each of its rows binds to
the session row it is about by `session_row: {seq, row_sha256, kind}`. The session's `session_end` row names the
ledger's last row (`ledger.head_seq`, `ledger.head_sha256`), so each chain fixes the other.

## Fields on every row (both files)

| Field | Type | Method |
|---|---|---|
| `run_id` | UUID string | `uuid.uuid4()` once per session. `session_id` (`TC-…`) still names the folder and V1's rows. |
| `source` | `real` \| `sim` | `real` for the rig and the dry run's fake bus (the arm path is the rig's); Part 2's simulator writes `sim`. |
| `policy_hash`, `policy_name` | sha256, string | `runner/policy_source.py`: sha256 over the files LeRobot runs. |
| `runner_hash` | sha256 | sha256 over (path, file sha256) of the runner's files, in order. |
| `t_wall` | float, s | `time.time()` when the row was appended, on the host named by `stamped_by`. |
| `t_mono` | float, s | `time.monotonic()` at the same moment: comparable only with rows stamped by the same host. |
| `stamped_by` | host id | `ledger.watchdog_host` in config (an id such as `mac`, never a machine name, RULES.md 7). |

## Ledger rows

Every ledger row also carries `schema: toceta-ledger/0.3`, `extends: v2-ledger/event/0.2`, `kind` and V2's chain fields
`seq`, `prev_sha256` and `row_sha256`.

### `identity` (once, at `session_start`)

| Field | Method |
|---|---|
| `source`, `policy_hash`, `policy_name`, `runner_hash` | as above |
| `device_ids.camera` | the stream's `device_id` (`opencv:<index>:<name>`, from `cameras/server.py`), or `clip:<sha256[:12]>` for the recorded clip |
| `device_ids.arm` | `<arm.adapter>:<arm.follower.id>` from config |
| `device_ids.watchdog_host`, `device_ids.policy_host` | `ledger.watchdog_host`, `ledger.policy_host` from config |

### `clock_sync` (at the start, every `ledger.clock_sync_every_s`, at the end)

| Field | Method |
|---|---|
| `hosts` | `{watchdog, policy}` |
| `offset_ms` | the policy host's clock minus the watchdog host's. One host: 0 by construction. Two hosts: n UDP pings to the policy host's clock peer (`python -m ledger.events_ext --clock-peer`). For each, t0 and t3 on this host's `time.time()` and t_peer on the peer's; `offset = t_peer - (t0 + t3) / 2` from the ping with the shortest round trip (NTP's rule). |
| `uncertainty_ms` | that round trip / 2: the offset is exact only if both legs took equally long |
| `rtt_ms`, `n` | the shortest round trip and the pings sent |
| `drift_ppm` | the change in `offset_ms` since the previous `clock_sync` row, over the wall time between them; null on the first |
| `error` | only when no ping was answered: `offset_ms` is then null, never a guess |

A latency measured across hosts is good to `uncertainty_ms` plus the drift since the nearest `clock_sync` row.

### `intervention` (a person acted)

| Field | Method |
|---|---|
| `who` | `--operator`, or `dry` in a dry run |
| `how` | `key` (a key accepted at V1's freeze menu) or `kill_switch` (V1's monitor 6: Esc or SIGINT) |
| `key`, `action` | `w` wait, `c` continue, `l` leave_as_is, `k` kill_torque_off; null and `stop` for the kill switch |
| `state` | `q6`, the arm read at the key (or the freeze row's own `q6` if the bus cannot be read, as `q6_source` says), `phase`, `freeze_seq` and `freeze` (the monitor or code that held the arm), and `refused` when V1 refused the key |
| `session_row` | the key row (`how: key`) or the freeze row (`how: kill_switch`) |

### `reset` (the arm given back to the policy after an intervention)

| Field | Method |
|---|---|
| `how`, `by` | `[c]: the policy goes on from where the arm is`, the operator |
| `intervention_seq` | the ledger row of the `[c]` |
| `time_to_reset_s` | `time.monotonic()` from the freeze row that held the arm to this row |
| `in_distribution` | `ok`, and the margin of every joint to the dataset's `observation.state` min..max (all episodes), widened by `ledger.in_distribution_margin_deg`. A reset outside that range is written with `ok: false`, not left out. |
| `session_row` | the `[c]` key row |

A `[k]` or `[l]` ends the run, so its reset, the arm brought back to the folded start, falls outside the session.
The next session's start check (within V1's 15° of the start pose) is where that shows. 0.3 does not link the two.

## Named timestamps (Part 2, M6)

A hold has five named moments, all `time.time()` on the watchdog host (the rows' `stamped_by`). `ledger/stamps.py`
recovers them per hold from what a session already logs (the runner is frozen until 3 Nov); the camera recorder
(`cameras/recorder.py`, a process of its own) adds the capture time.

| Name | The moment | Where it is read | In a TC- session now |
|---|---|---|---|
| `t_capture` | the frame's capture: the camera server's stamp as soon as OpenCV hands it over (`cameras/server.py`), later than the exposure by readout and driver buffering | the camera recorder's row (`<session>/camera/index.jsonl`) for the server frame the hold's pump frame was made from, matched by pixels (`ledger/frames.py`, `MATCH_METHOD`, with its error) | **null** in the 27 sessions so far (no recorder ran); read where it ran |
| `t_detect` | V1's hand monitor decides about the frame | the hand message's `t` (V1 `monitor/hand_monitor.py`, `wire.build_hand_msg`), in V1's clip, matched by the freeze row's `frame_seq` and `hand_seq` | read |
| `t_hold_packet` | the datagram carrying that decision is sent | V1 sends no separate hold packet: the runner holds on the hand message itself, stamped with `t` just before `sendto` (a JSON encode between, under 1 ms) | read, = `t_detect` |
| `t_goal_frozen` | the first runner row with the goals frozen | the freeze row's `t_wall`; V1's `t_hold` (`frame_t + t_hold_minus_frame_t_ms`) and the motion log's `freeze_hold_present` are kept beside it as checks | read |
| `t_velocity_zero` | the first joint reading with every arm joint below 1 deg/s | the first step row after the hold that shows it | **null**: V1 reads the arm once, about 5 ms after the hold write, then waits at its menu without reading it; the bound is the next reading (the key's `q6`) |

The proxy for the first one is `t_frame_pump`, V1's frame pump `frame_t`: after the frame left the camera stream and
was JPEG-encoded, so later than `t_capture` by exposure, readout, driver buffering and the stream (the stream alone is
`session_end.camera_stream.latency_ms`, about 5 ms at p50).

**The public frame-to-hold number is `t_hold_packet - t_capture`.** With `t_capture` null it is null, and its lower
bound `t_hold_packet - t_frame_pump` is reported in its place, named as a bound. ISO 13855's stop chain T is
`t_velocity_zero - t_capture`; with the standstill not logged, its lower bound is `t_goal_frozen - t_capture`, or
`t_goal_frozen - t_frame_pump` without the recorder (`stop_chain_ms`). V1's own `frame_to_hold_ms`
(`t_hold - frame_t`) is a different interval: it ends at the hold write, not at the packet.

Every hold reports the four deltas between consecutive stamps (`t_detect-t_capture`, `t_hold_packet-t_detect`,
`t_goal_frozen-t_hold_packet`, `t_velocity_zero-t_goal_frozen`), null where a stamp is, and the two proxies
`t_detect-t_frame_pump` and `t_goal_frozen-t_frame_pump`. Over a session (or a record) each is given as n, p50 and p95
with a 95 % interval, and the max (`ledger/stats.py`): the interval is distribution-free, the sorted values at the ranks
the Wilson score interval for the percentile gives, so it says how few holds a number rests on.

## Windows (`toceta-window/0.1`, Part 2 M6)

`ledger/window.py` cuts one window per trigger of a recorded session to `windows/<anchor row sha256>/` (git-ignored: the
frames show people). A trigger is a hold (freeze row), a kill row, an intervention (ledger row), a shadow (a monitor
that is off by config, or V1's box rule under the distance rule, that V1 would have fired: one per run of such steps,
runs closer than 1 s merged) or a step the operator marked as a miss in `<session>/sheet.yaml`. A row with several
reasons is one window.

| Part | Holds |
|---|---|
| `streams/joints.jsonl` | the step rows: `q6`, the commanded `target6`, `tcp_mm`, load, `dt_ms`, phase, the `policy` block (this step's action, its chunk and the steps left) |
| `streams/monitors.jsonl` | every monitor's value and threshold per step, monitors that are off included, recomputed from the row with the limits its `session_start` logged, beside V1's `fired`, `off`, `v1_would_fire` |
| `streams/hand_track.jsonl` | per detection: the hand message's time, every hand's palm, box (from its landmarks), confidence, handedness, `in_workspace`, landmarks, and the message itself as V1's hand monitor published it (`msg`, what the runner was sent); `frame_seq`
names its frame in this window's `frames/` (with the recorder's frames, the one matched to the pump frame, its `t_capture` and `match_error_ms` beside it) |
| `streams/frames.jsonl`, `frames/<seq>.jpg` | the frames in the interval from one store, named by `frames_from`: `camera_server`, the camera recorder's (the server's own JPEG bytes, `channels: rgb_as_bgr`), when it kept any there, else `hand_monitor`, V1's clips (the frame pump's JPEGs, `channels: bgr`); each with its sha256 and the sha256 of the frame it came from |
| `streams/events.jsonl` | the non-step rows of the session, the ledger and V1's motion log in the interval, and the hold state they imply |
| `manifest.json` | lengths, rates, clocks, `stamped_by` and sha256 per stream; the interval and whether it is complete, and why not; the camera settings (exposure and white balance: null, not logged) and the registration frame's luminance; the registration in force (`session_start.table_map`); the session's `clock_sync` rows; the hold's named stamps; the action chunk (null: the runner logs one action per step, not the chunk); `frames_from` and `frames.channels`; faces; the start sheet |

`window_hash` is the sha256 of the manifest as canonical JSON. The manifest names every file's sha256 and nothing about
when it was cut, so the hash fixes every byte and cutting again gives the same hash. A window is `complete` only when
its frames, joint rows and hand track reach both ends of the interval; V1 keeps 3 s of frames before a signal, so with
`pre_s` 5 no window of these sessions is complete, and each says so. The recorder keeps every frame, but the hand track
is still V1's clips until V1 logs every message (after 3 Nov). Faces: `window.blur_faces` in the run config, `--blur-faces`,
or `site: external` in the session's sheet blurs every face OpenCV's frontal-face cascade finds, and the manifest
records that it did; the default on the bench is off.

## The camera recorder (`toceta-camera-recorder/0.1`)

`cameras/recorder.py` subscribes to the loopback camera stream (it binds nothing, sends nothing and opens no camera or
port) and, while the newest TC- session has no `session_end` row, writes into `<session>/camera/`:

| File | Holds |
|---|---|
| `seg_<n>.mjpeg` | the server's JPEG bytes, as published, one after another (`recorder.segment_frames` per file) |
| `index.jsonl` | a chain (V1's rule: `seq`, `prev_sha256`, `row_sha256`): a `recorder_start` row (its settings, the run config's name and hashes, the `t_capture` method), one `frame` row per frame (`server_seq`, `t_capture_wall`, `t_capture_mono`, `t_send_mono`, `t_recv_wall`, `t_recv_mono`, its segment, offset, length and sha256, `missed` since the last one, `pre_roll`), and a `recorder_end` row with why it stopped |

The last `pre_roll_s` of frames before a session's folder appears are written first. A window checks each frame's
bytes against its row's sha256 when it uses them. The JPEGs hold the RGB array as OpenCV would hold BGR (as LeRobot's
image_server does): `ledger/frames.py decode()` swaps the channels back.

## How V2 reads them (`ledger/ingest.py`, Part 2 M6)

V2 is not changed. Its tools read TC- sessions through the sessions-root argument they already take
(`--root toceta_live_tote=<repo>/out/live/sessions`): `field_record.py` and `verify_record.py` find a session by name
under a root. `events.py`'s command line lists only `KH-S*` folders, so `ledger/ingest.py` imports its `Session` and
`events_of` and writes what they return to `out/v2/events/<session>.events.jsonl`, which `label.py --events-dir`
reads.

1. V2's events are kept as V2 makes them. Each gains `window_hash` (additive; no chain rule changes, no row is
   edited), and a freeze gains `stamps`, its named timestamps.
2. Toceta's own types follow V2's envelope with `schema: toceta-ledger/0.3`: `intervention` and `reset` from
   `ledger.jsonl` (each names `v2_event_id`, V2's `operator_hold` for the same key, so one act is one act), `shadow`
   (monitor, `would_have_fired`, value, threshold, the run's length, `counts_as_freeze: false`), and `kill` only for a
   kill row no V2 event links. **A shadow never counts as a freeze**: V2's records re-extract freezes from the chains,
   and label.py asks nothing about a shadow.
3. A marked step becomes a V2 review window (`v2-ledger/window/0.1`, category `M`, `out/v2/windows/`), which label.py
   asks "Was this a miss?" about, and which field_record.py counts through `--windows-dir`.
4. The labels chain is `ledger/labels/labels.jsonl`, appended only by V2's own label.py.
5. `--record` builds the session's field record with V2's `field_record.py` (imported) and adds a top-level `policy`
   section (`ledger/policy_record.py`): policy name and hash, dataset id and version, training steps, runner hash, the
   run config's `monitors:` block verbatim (from git history when the config has changed since), the frame-to-hold and
   ISO 13855 sentences with the session's own numbers. It is not a row, so V2's `verify_record.py` still verifies the
   record; the section binds to the record's head and is checked by rebuilding it from the session.
6. V1's own KH-S sessions predate the 0.3 stamps: ingest gives them V2's events unchanged and no windows (tested equal
   to V2's own `events_of`).
