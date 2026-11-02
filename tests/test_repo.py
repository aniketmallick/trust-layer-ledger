"""What this repository claims, tried against a copy of it. Standard library only.

    python3 -m unittest discover tests

A verifier that has only ever said yes proves nothing, so most of these change something and expect it to be caught.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "verifier"))
import chain  # noqa: E402
import verify_all  # noqa: E402

SMALL = "TC-S20261028T020300"                      # a policy run with two clips and few rows
KNIFE = "KH-S20261004T174200"                      # a knife session, in the 30-day record


class Copy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        self.record = self.root / "records" / "policy" / SMALL / "field_record.json"
        self.session = self.root / "sources" / "toceta_live_tote" / SMALL

    def verified(self, record: Path | None = None) -> tuple[bool, str]:
        ok, lines, _ = verify_all.check(record or self.record, self.root)
        return ok, "\n".join(lines)

    def rec(self) -> dict:
        return json.loads(self.record.read_text(encoding="utf-8"))

    def save(self, rec: dict) -> None:
        self.record.write_text(json.dumps(rec), encoding="utf-8")


class Passes(Copy):
    def test_the_untouched_copy_verifies(self):
        ok, out = self.verified()
        self.assertTrue(ok, out)
        self.assertIn("rebuilt from the sources: identical", out)
        self.assertIn("2 clip files withheld", out)


class Caught(Copy):
    def test_one_changed_character_in_a_session_log(self):
        p = self.session / "session.jsonl"
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace('"phase": "POLICY"', '"phase": "POLICX"', 1), encoding="utf-8")
        ok, out = self.verified()
        self.assertFalse(ok)
        self.assertIn("does not verify", out)

    def test_a_deleted_row_in_a_session_log(self):
        p = self.session / "session.jsonl"
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        p.write_text("".join(lines[:40] + lines[41:]), encoding="utf-8")
        self.assertFalse(self.verified()[0])

    def test_a_session_log_rebuilt_with_a_valid_chain_but_a_new_head(self):
        p = self.session / "session.jsonl"
        rows = chain.read_rows(p)
        p.unlink()
        for r in rows[:-1]:
            chain.append_row(p, r)
        ok, out = self.verified()
        self.assertFalse(ok)
        self.assertIn("its head is not the one the record names", out)

    def test_an_edited_row_in_a_record(self):
        rec = self.rec()
        m = next(r for r in rec["rows"] if r["kind"] == "metric" and r["name"] == "armed_hours")
        m["value"] = 100.0
        self.save(rec)
        ok, out = self.verified()
        self.assertFalse(ok)
        self.assertIn("its chain does not verify", out)

    def test_a_forged_number_with_the_whole_chain_redone(self):
        rec = self.rec()
        m = next(r for r in rec["rows"] if r["kind"] == "metric" and r["name"] == "armed_hours")
        m["value"] = 100.0
        prev = None
        for r in rec["rows"]:
            r["prev_sha256"] = prev
            r["row_sha256"] = chain.row_hash(r)
            prev = r["row_sha256"]
        rec["head"] = {"seq": rec["rows"][-1]["seq"], "row_sha256": prev}
        self.save(rec)
        ok, out = self.verified()
        self.assertFalse(ok)
        self.assertIn("differs from the record", out)

    def test_an_edited_policy_section(self):
        rec = self.rec()
        rec["policy"]["judge"] = "JUDGE ON"
        self.save(rec)
        ok, out = self.verified()
        self.assertFalse(ok)
        self.assertIn("policy section: its sha256 does not match", out)

    def test_a_policy_section_moved_from_another_record(self):
        other = self.root / "records" / "policy" / "TC-S20261028T115437" / "field_record.json"
        rec = self.rec()
        rec["policy"] = json.loads(other.read_text(encoding="utf-8"))["policy"]
        self.save(rec)
        ok, out = self.verified()
        self.assertFalse(ok)
        self.assertIn("bound to another record head", out)

    def test_a_clip_put_back_with_other_bytes(self):
        clips = self.session / "v2_clips"
        name = sorted(clips.glob("*.json"))[0].stem
        (clips / f"{name}.mjpeg").write_bytes(b"not those frames")
        ok, _ = self.verified()
        self.assertFalse(ok)

    def test_a_changed_label(self):
        p = self.root / "labels" / "labels.jsonl"
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace('"label": "false_alarm"', '"label": "true_positive"', 1), encoding="utf-8")
        ok, out = self.verified(self.root / "records" / "window_30d" / "field_record.json")
        self.assertFalse(ok)
        self.assertIn("labels:", out)

    def test_a_missing_session_log(self):
        shutil.rmtree(self.root / "sources" / "knife_handover" / KNIFE)
        ok, out = self.verified(self.root / "records" / "window_30d" / "field_record.json")
        self.assertFalse(ok)
        self.assertIn("not on disk", out)


class NoFrames(unittest.TestCase):
    FILES = [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts]

    def test_no_image_or_video_file(self):
        bad = [p for p in self.FILES if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".mjpeg", ".mp4", ".gif", ".webp")]
        self.assertEqual(bad, [])

    def test_no_embedded_image_data(self):
        pat = re.compile(rb"data:image/|/9j/4AAQ|iVBORw0KGgo")
        data = [ROOT / d for d in ("records", "sources", "labels")]
        bad = [p for p in self.FILES if any(d in p.parents for d in data) and pat.search(p.read_bytes())]
        self.assertEqual(bad, [])

    def test_every_field_record_page_has_its_event_page(self):
        for p in (ROOT / "records").rglob("field_record.html"):
            if "event.html" in p.read_text(encoding="utf-8"):
                self.assertTrue((p.parent / "event.html").is_file(), p)


class Words(unittest.TestCase):
    def test_the_first_line_of_the_readme(self):
        first = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("evidence, not certification", first.lower())

    def test_the_verifier_needs_nothing_outside_the_standard_library(self):
        mine = {p.stem for p in (ROOT / "verifier").glob("*.py")}
        for p in list((ROOT / "verifier").glob("*.py")) + [ROOT / "verify_all.py"]:
            for m in re.findall(r"^(?:from|import) (\w+)", p.read_text(encoding="utf-8"), re.M):
                self.assertTrue(m in sys.stdlib_module_names or m in mine, f"{p.name}: {m}")

    def test_the_licence_is_apache_2(self):
        self.assertIn("Apache License", (ROOT / "LICENSE").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
