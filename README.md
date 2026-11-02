**Evidence, not certification.** This repository shows what one robot arm did on a bench and lets you check it. It does not say the arm, its software or anything built on them is fit for any use.

# trust-layer-ledger

[trust-layer-demo](https://github.com/aniketmallick/trust-layer-demo) is a demonstration: an SO-101 arm, one overhead
camera, and a layer of code that stops the arm when a hand gets in the way, a joint leaves its range or a vision model
objects. A video of it stopping once tells you very little. What you want to know is how often it fired, how often it
fired for nothing, how quickly the hold went out, and what it missed.

This repository is what I have on those questions. The records are built from the arm's own session logs, where every
row carries the hash of the row before it. The logs are here too, without the camera frames, so you can rebuild the
numbers instead of taking my word for them.

There are 28 records:

- `records/window_30d/` covers a scripted screwdriver handover: 13 sessions, 1.07 armed hours.
- `records/policy/` holds one record for each of 27 sessions of a learned policy moving an item from a tote to a tray,
  about 20 armed minutes in all.

## Check it

You need Python and nothing else. It is tested on 3.12 and 3.13.

```
git clone https://github.com/aniketmallick/trust-layer-ledger
cd trust-layer-ledger
python3 verify_all.py
```

It takes a few seconds and ends with:

```
28 of 28 records verified
scope: 13 of the 94 sessions are in this record; the other 81 are in m1_left_out.json, and their logs were not read
40 clip files were not checked: they are not published. Their hashes are in the chains.
```

The exit code is 0 if every record verified and 1 if any did not. Now open any file under `sources/`, change one digit
and run it again.

For each record the verifier walks the record's own hash chain, walks each source log's chain and compares its head with
the one the record names, finds the row of the labels chain the record was built up to, and then rebuilds every row from
the logs and labels and compares it with what the record says. That last step is the one that counts. Changing a number
and redoing the hashes is easy, and it still fails the rebuild.

`python3 -m unittest discover tests` makes thirteen such attempts on a temporary copy, including a forged number with its
whole chain redone, a deleted log row, a changed label and a policy section moved from another record. It takes under a
minute.

## Reading a record

Open `field_record.html` in a browser. It has the indicators, a timeline, hold latency, stops by source, and a map onto
regulations and standards with a column for what the record does not show. Click an event for its page, which has the
monitor lanes, the rows either side of it, the judge's call and the label. Those pages say where a frame would be.
`field_record.json` is the same content as hash-chained rows. [SCHEMA.md](SCHEMA.md) describes it.

Every number in a record is a row with its window, count, denominator, label coverage and method. Labels are counted by
who made them. Only mine, given one event at a time at a prompt, count as ground truth. Proposals I accepted in bulk and
labels from a model panel are shown separately and never feed a rate. A rate is printed only where my labels cover at
least half of the events; otherwise the record has a `not_shown` row saying why.

## The 30-day record

Before the numbers, three things about what is in it.

It is a selection. The window is 30 days, but the sessions in it ran on four days, 2 to 5 October 2026, and they are the
13 I accepted into the first extraction (`runs/m1_first13.txt`). Those four days produced 94 sessions. The other 81 hold
1.94 armed hours and 77 freezes, and 38 of them have no armed time at all. The record therefore covers about a third of the
arm's time in those days, and none of its numbers is a rate for the period as a whole.

The record says this itself, in a `scope` row right after its header and at the top of its page. The row is part of the
chain, and a window record without one does not verify. `runs/m1_left_out.json` lists the 81 sessions with each one's chain
head, armed seconds and freezes, and the verifier recomputes the totals from that file.

Most of the code was not under test. 10 of the 13 sessions ran under the operator override, which means that exact code had
not passed the test gate. The code also changed between sessions, and each `source` row gives that session's code hash. 2
of the 13 ran with the layer off on purpose.

About an hour is not much. With 1.07 armed hours as the denominator, one event moves a rate per 100 hours by about 94.

| Measure | Result | Method |
|---|---|---|
| Events | 179 | 45 freezes, 31 judge vetoes, 62 planner refusals, 18 operator holds, 11 releases, 12 override stamps |
| Freezes | 31 true, 5 false alarm, 9 unclear | all 45 labelled by me, one at a time |
| False alarms | 468.65 per 100 armed hours | 5 over 1.07 h |
| Frame to hold | p50 69.6, p95 132.1, max 132.8 ms | 35 hand-monitor freezes; encoded frame to the write of the hold goal |
| Stops by source | planner 62, hand monitor 35, judge 31, operator 18, envelope 6, code 4 | one count per event |
| Judge vetoes | no rate printed | my labels cover 3 % |
| Planner refusals | no rate printed | my labels cover 27 % |
| Releases | no outcome rate printed | my labels cover 18 % |
| Misses | no count printed | I reviewed 0 of the 59 sampled windows |

A model panel did review those 59 windows: 36 correct, 23 unclear, 0 misses. That is only a reason to go and look. The hand
monitor is known to lose hands in plain view, so "no miss found" means little here.

The result I would look at first is in the releases. Of the 6 automatic releases, 2 are labelled "no"
(`KH-S20261004T020430#1302` and `KH-S20261004T021223#1913`): the judge did not object, the gripper opened, and a second
later the screwdriver was on the table. Those two labels were accepted in bulk, so they feed no rate, and the frames that
show it are not published.

## The learned-policy records

These are all 27 sessions of an ACT policy (`anikmall/act_toceta_tote_tray@7422a89b7764`) on the real arm, 27 and 28
October 2026, including the ones that failed to start. One of them, `TC-S20261028T022820`, stopped at a motor check before
its session began, so its policy fields are empty.

| How it ended | Sessions |
|---|---|
| The run reached its end | 9 |
| I stopped it (kill, torque off) | 10 |
| Connection error | 5 |
| Did not start: torque not on, not confirmed, or a motor check | 3 |

"Reached its end" does not mean the item reached the tray. No row in these logs says whether the task worked, and none of
the 54 events has been labelled, so these records hold no false-alarm rate and no success rate.

Large parts of the layer were switched off in most of these runs, which was my call for a first hands-out trial:

| Sessions | Armed | Off | Hand rule | Judge |
|---|---|---|---|---|
| 6 | 268 s | hand monitor and its link (7, 8), plus gripper overload (4) in 4 of them | none | off |
| 2 | 55 s | gripper overload (4) | any hand in the box | on |
| 1 | 85 s | joint margin, workspace box, gripper overload, loop time (1, 3, 4, 5) | any hand in the box | on |
| 17 | 778 s | the same four | a hand within 50 mm (13 sessions), 100 mm (2) or 150 mm (2) | off |

Monitors 1, 3, 4 and 5, when switched off by config, are still read at every step; they just cannot stop the arm. Each
record says what was off in its `policy` section, and the logs stamp it on every step.

In those runs there were 24 freezes: 8 from the hand monitor, 6 internal errors, 4 gripper overloads, 4 kill-switch presses
and 2 joint margins. There were also 20 operator holds, 4 judge vetoes and 14 kills, all by me. The 8 hand-monitor holds took
43.3 to 85.9 ms from frame to hold. That is a lower bound, because no camera recorder ran and capture time was not logged.

The distances in that table are demonstration settings. Each record works out the ISO 13855 separation for its own loop:
at 100 mm, for example, session `TC-S20261028T115437` gets at least 393.4 mm for a normal approach and at least 998.3 mm for
a parallel one, with the detection capability assumed rather than measured. The thresholds fall well short of those
figures, and the records say so.

## What a pass does not show

The 81 left-out sessions are not checked. Their logs are not published, so the verifier adds up what
`runs/m1_left_out.json` says and goes no further. It cannot tell you that any one session's figures are right. All 81 chains
verified when the list was made, and their heads are in it, so a log published later would have to match.

The frames are not checked. A clip's hash is in the chain but its bytes are not here, so `verify_all.py` counts those 40
files instead of checking them. The stored photos are the same: their hashes are in the logs and the files are not
published. Anything that depended on seeing a frame, including all of my labels, you are taking from me.

Nothing is signed or externally timestamped. The chains show an edit made after the fact. They would not show someone
regenerating all the logs and all the records together. The three public sessions in the 30-day record are a partial
check on that: they have been in trust-layer-demo since 5 October 2026 and are byte-identical there. The other logs have
only this repository's first push as a public date.

A log records what the code wrote down, so a bug that wrote a wrong value writes it into a valid chain. The hold is timed
to the write of the hold goal, so the records do not cover the servos coming to rest or stopping distance. Nothing outside
the camera's view is in them, and neither is a hand the arm hides from it.

The `policy` and `twin` sections are only hash-checked. `verify_all.py` confirms that they are unedited and bound to their
record. It does not rebuild them, since that needs the run config, the model's `train_config.json` and the simulator's
chain, which are not here. The rows of those records are fully rebuilt.

A map onto a standard is not a claim of meeting it. The records list EU 2023/1230, UL 4600, ISO 10218 and ISO/TS 15066 next
to what the record shows and what it does not. Nothing here has been assessed against any of them.

## What is not published

No camera frames, clips, photos or video, and so no faces. `tests/test_repo.py` fails if an image file or embedded image data
turns up, and `.gitignore` refuses the formats. Hand data appears only as keypoints and table coordinates.

The logs are published as they were written, because editing a row would break its chain. They name me as the operator and
labeller, and some policy logs contain a local file path and the arm's USB serial number in check rows and error messages.

## Layout

| Path | What |
|---|---|
| `verify_all.py` | checks every record |
| `verifier/` | the code that builds and checks records |
| `records/window_30d/`, `records/policy/<session>/` | `field_record.json`, `field_record.html`, `event.html` |
| `sources/knife_handover/` | the 13 session logs behind the 30-day record |
| `sources/toceta_live_tote/` | the 27 policy session logs, ledgers and clip metadata |
| `labels/` | the label chains; the policy one is empty |
| `runs/m1_first13.txt` | the 13 sessions, as chosen |
| `runs/m1_left_out.json` | the other 81, each with its chain head, armed time and freezes |
| `schema/`, `SCHEMA.md` | the formats |
| `tools/build_event_pages.py` | rebuilds the frame-free `event.html` pages |
| `tests/` | the tampering tests |

## Where this came from

`verifier/` is copied unchanged from the private working repository at commit `016c0e9`. The `field_record` files are as
built there and in the policy runner's repository, which is also where `schema/toceta-ledger-0.3.md` comes from, unchanged,
so its paths refer to files in that repository. `event.html` is rebuilt here without frames.

The policy records' `built` blocks name older commits. Their rows did not change, which is what the rebuild shows.

## Licence

Apache 2.0, as for trust-layer-demo.
