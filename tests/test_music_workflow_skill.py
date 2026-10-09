import hashlib
import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
import zipfile
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1] / "skills" / "ai-music-workflow"
SCRIPT = SKILL / "scripts" / "project.py"
DELIVERY_FILES = ("README.md", "SKILL.md", "agents/openai.yaml", "references/suno-workflow.md", "scripts/project.py")
LYRICS = "[Verse]\n\u98ce\u5439\u8fc7\u65e7\u8857\n\n[Chorus]\n\u6211\u4eec\u56de\u5bb6\n"


class SkillCase(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "Standalone skill script is not implemented yet")
        spec = importlib.util.spec_from_file_location("music_skill_project", SCRIPT)
        self.p = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.p)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"

    def text(self, name, content):
        path = self.base / name
        path.write_text(content, encoding="utf-8")
        return path

    def json_file(self, name, value):
        return self.text(name, json.dumps(value, ensure_ascii=False))

    def init(self):
        return self.p.init_project(self.root, "Test song", self.text("brief.txt", "A hopeful song"))

    def manifest(self):
        return json.loads((self.root / "project.json").read_text(encoding="utf-8"))

    def cli(self, *args, script=SCRIPT):
        return subprocess.run([sys.executable, "-I", "-S", str(script), *map(str, args)],
                              capture_output=True, text=True, encoding="utf-8", timeout=15)


class SkillProjectTest(SkillCase):
    def test_init_and_resume(self):
        state = self.init()
        self.assertEqual(state["schema_version"], 1)
        out = self.cli("status", "--project", self.root)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["project_id"], state["project_id"])
        self.assertEqual(self.manifest()["final_selection"], [])
        with self.assertRaises(self.p.ProjectError):
            self.init()

    def test_lyrics_are_immutable(self):
        self.init()
        source = self.text("lyrics.txt", LYRICS)
        first = self.p.add_lyrics(self.root, source)
        source.write_text("Changed", encoding="utf-8")
        second = self.p.add_lyrics(self.root, source)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual((self.root / first["path"]).read_text(encoding="utf-8"), LYRICS)

    def test_approval_binds_hash(self):
        self.init()
        lyric = self.p.add_lyrics(self.root, self.text("lyrics.txt", LYRICS))
        approved = self.p.approve_lyrics(self.root, lyric["id"], self.text("approval.txt", "Approved lyrics"))
        self.assertEqual(approved["approval"]["sha256"], hashlib.sha256(LYRICS.encode()).hexdigest())
        (self.root / lyric["path"]).write_text("Tampered", encoding="utf-8")
        with self.assertRaises(self.p.ProjectError):
            self.p.approve_lyrics(self.root, lyric["id"], self.text("approval.txt", "Approved"))

    def test_atomic_failure_preserves_manifest(self):
        self.init()
        before = (self.root / "project.json").read_bytes()
        with patch.object(self.p.os, "replace", side_effect=OSError("simulated disk error")):
            with self.assertRaises(self.p.ProjectError):
                self.p.add_lyrics(self.root, self.text("lyrics.txt", LYRICS))
        self.assertEqual((self.root / "project.json").read_bytes(), before)
        self.assertFalse((self.root / ".skill-project.lock").exists())

    def test_existing_lock_rejects_write(self):
        self.init()
        lock = self.root / ".skill-project.lock"
        lock.write_text("another writer", encoding="utf-8")
        with self.assertRaises(self.p.ProjectError):
            self.p.add_lyrics(self.root, self.text("lyrics.txt", LYRICS))
        self.assertEqual(lock.read_text(), "another writer")
        self.assertEqual(self.manifest()["lyrics"], [])

    def test_transient_windows_manifest_replace_recovers(self):
        self.init()
        real_replace = self.p.os.replace
        attempts = []

        def briefly_locked(source, target):
            attempts.append((source, target))
            if len(attempts) < 3:
                raise PermissionError(13, "Synthetic Windows sharing denial", None, 5)
            return real_replace(source, target)

        with patch.object(self.p.sys, "platform", "win32"), patch.object(self.p.os, "replace", side_effect=briefly_locked):
            try:
                lyric = self.p.add_lyrics(self.root, self.text("lyrics.txt", LYRICS))
            except self.p.ProjectError as exc:
                self.fail("Transient sharing failure should recover: " + str(exc))
        self.assertEqual(self.manifest()["lyrics"][0]["id"], lyric["id"])
        self.assertEqual(len(attempts), 3)
        self.assertFalse((self.root / ".skill-project.lock").exists())

    def test_persistent_windows_denial_preserves_manifest_and_stops(self):
        self.init()
        before = (self.root / "project.json").read_bytes()
        denied = PermissionError(13, "Synthetic persistent denial", None, 5)
        with patch.object(self.p.sys, "platform", "win32"), patch.object(self.p.os, "replace", side_effect=denied) as operation:
            with self.assertRaises(self.p.ProjectError):
                self.p.add_lyrics(self.root, self.text("lyrics.txt", LYRICS))
        self.assertLessEqual(operation.call_count, 5)
        self.assertEqual((self.root / "project.json").read_bytes(), before)
        self.assertFalse((self.root / ".skill-project.lock").exists())

    def test_non_windows_permission_error_is_not_retried(self):
        self.init()
        with patch.object(self.p.sys, "platform", "linux"), patch.object(
                self.p.os, "replace", side_effect=PermissionError(13, "Synthetic ACL denial")) as operation:
            with self.assertRaises(self.p.ProjectError):
                self.p.add_lyrics(self.root, self.text("lyrics.txt", LYRICS))
        self.assertEqual(operation.call_count, 1)

    @unittest.skipUnless(os.name == "nt", "Real Windows file sharing semantics")
    def test_real_windows_reader_lock_recovers(self):
        self.init()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(self.root / "project.json"), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value, ctypes.get_last_error())
        timer = threading.Timer(0.12, kernel.CloseHandle, args=(handle,))
        timer.start()
        try:
            try:
                lyric = self.p.add_lyrics(self.root, self.text("lyrics.txt", LYRICS))
            except self.p.ProjectError as exc:
                self.fail("A temporary real Windows reader should not lose the write: " + str(exc))
        finally:
            timer.join()
        self.assertEqual(self.manifest()["lyrics"][0]["id"], lyric["id"])

    def test_paths_cannot_escape_project(self):
        self.init()
        outside = self.text("outside.txt", "private")
        for path in ("../outside.txt", str(outside)):
            state = self.manifest()
            state["brief_path"] = path
            (self.root / "project.json").write_text(json.dumps(state), encoding="utf-8")
            with self.subTest(path=path), self.assertRaises(self.p.ProjectError):
                self.p.project_status(self.root)

    def test_project_inside_skill_is_rejected(self):
        target = SKILL / "forbidden-project"
        with self.assertRaises(self.p.ProjectError):
            self.p.init_project(target, "No", self.text("brief.txt", "Brief"))
        self.assertFalse(target.exists())

    def test_unknown_schema_and_missing_files(self):
        self.init()
        (self.root / "brief.md").unlink()
        self.assertEqual(self.p.project_status(self.root)["next_action"], "repair_files")
        state = self.manifest()
        state["schema_version"] = 99
        (self.root / "project.json").write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(self.p.ProjectError):
            self.p.project_status(self.root)

    def test_secret_input_not_echoed(self):
        secret = "api_key=sk-" + "x" * 24
        out = self.cli("init", "--project", self.root, "--title", "Private",
                       "--brief-file", self.text("brief.txt", secret))
        self.assertEqual(out.returncode, 2)
        self.assertNotIn(secret, out.stdout + out.stderr)
        self.assertFalse((self.root / "project.json").exists())


class BatchCase(SkillCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(hasattr(self.p, "prepare_batch"), "Batch records are not implemented yet")
        self.init()
        self.lyric = self.p.add_lyrics(self.root, self.text("lyrics.txt", LYRICS))
        self.p.approve_lyrics(self.root, self.lyric["id"], self.text("approval.txt", "Approved"))

    def package(self, **changes):
        data = dict(title="Test song", style="Acoustic", lyrics_id=self.lyric["id"],
                    lyrics=LYRICS, instrumental=False)
        data.update(changes)
        return self.json_file("package.json", data)

    def auth(self, **changes):
        data = dict(approval="User approved this package and one generation click", max_generation_clicks=1)
        data.update(changes)
        return self.json_file("authorization.json", data)

    def batch(self):
        return self.p.prepare_batch(self.root, self.package(), self.auth())

    def observe(self, batch, status, ids=None, evidence="Observed page state"):
        file = self.json_file("observation.json", dict(status=status, song_ids=ids or [], evidence=evidence))
        return self.p.record_submission(self.root, batch["id"], file)

class SkillBatchTest(BatchCase):
    def test_malformed_state_blocks_resume_and_generation(self):
        self.observe(self.batch(), "uncertain")
        original = self.manifest()
        variants = [dict(submission_status="uncertian"), dict(submission_status=None),
                    dict(song_ids="not-a-list"), dict(evidence=None), dict(authorization={})]
        for changes in variants:
            state = json.loads(json.dumps(original))
            state["batches"][0].update(changes)
            (self.root / "project.json").write_text(json.dumps(state), encoding="utf-8")
            before = (self.root / "project.json").read_bytes()
            with self.subTest(changes=changes):
                result = self.cli("status", "--project", self.root)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stderr)["error"], "schema")
                with self.assertRaises(self.p.ProjectError):
                    self.batch()
                self.assertEqual((self.root / "project.json").read_bytes(), before)
        state = json.loads(json.dumps(original))
        del state["batches"][0]["submission_status"]
        (self.root / "project.json").write_text(json.dumps(state), encoding="utf-8")
        result = self.cli("status", "--project", self.root)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_package_matches_approved_lyrics(self):
        batch = self.p.prepare_batch(self.root, self.package(lyrics=LYRICS.replace("\n", "\r\n")), self.auth())
        saved = json.loads((self.root / batch["package_path"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["lyrics"], LYRICS)
        self.assertEqual(batch["submission_status"], "prepared")

    def test_unapproved_or_changed_lyrics_blocked(self):
        draft = self.p.add_lyrics(self.root, self.text("draft.txt", "Unapproved"))
        with self.assertRaises(self.p.ProjectError):
            self.p.prepare_batch(self.root, self.package(lyrics_id=draft["id"], lyrics="Unapproved"), self.auth())
        (self.root / self.lyric["path"]).write_text("Changed", encoding="utf-8")
        with self.assertRaises(self.p.ProjectError):
            self.batch()
        self.assertEqual(self.manifest()["batches"], [])

    def test_instrumental_has_no_lyrics(self):
        with self.assertRaises(self.p.ProjectError):
            self.p.prepare_batch(self.root, self.package(instrumental=True), self.auth())
        with self.assertRaises(self.p.ProjectError):
            self.p.prepare_batch(self.root, self.package(instrumental=True, lyrics_id=None, lyrics=" \r\n\t"), self.auth())
        good = self.p.prepare_batch(self.root, self.package(instrumental=True, lyrics_id=None, lyrics=""), self.auth())
        self.assertIsNone(good["lyrics_id"])

    def test_generation_scope_is_explicit(self):
        for changes in (dict(approval=""), dict(max_generation_clicks=2), dict(max_generation_clicks=True)):
            with self.subTest(changes=changes), self.assertRaises(self.p.ProjectError):
                self.p.prepare_batch(self.root, self.package(), self.auth(**changes))

    def test_uncertain_submission_requires_reconciliation(self):
        batch = self.batch()
        self.observe(batch, "uncertain")
        out = self.cli("status", "--project", self.root)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["next_action"], "reconcile_suno")
        with self.assertRaises(self.p.ProjectError):
            self.observe(batch, "prepared")

    def test_pending_batch_blocks_new_batch(self):
        self.observe(self.batch(), "uncertain")
        with self.assertRaises(self.p.ProjectError):
            self.batch()
        self.assertEqual(len(self.manifest()["batches"]), 1)

    def test_submitted_requires_observed_ids(self):
        batch = self.batch()
        with self.assertRaises(self.p.ProjectError):
            self.observe(batch, "submitted", ["song-1"])
        self.observe(batch, "uncertain")
        with self.assertRaises(self.p.ProjectError):
            self.observe(batch, "submitted", [])

    def test_repeated_observation_is_idempotent(self):
        batch = self.batch()
        self.observe(batch, "uncertain")
        self.observe(batch, "submitted", ["song-1", "song-1"])
        self.observe(batch, "submitted", ["song-1"])
        saved = self.manifest()["batches"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["song_ids"], ["song-1"])

    def test_new_evidence_is_retained_without_changing_submission(self):
        batch = self.batch()
        self.observe(batch, "uncertain", evidence="First check")
        self.observe(batch, "uncertain", evidence="Second distinct check")
        self.observe(batch, "uncertain", evidence="Second distinct check")
        saved = self.manifest()["batches"][0]
        self.assertEqual([e["text"] for e in saved["evidence"]],
                         ["First check", "Second distinct check"])
        self.assertEqual(saved["submission_status"], "uncertain")
        self.observe(batch, "submitted", ["song-1"], "Result observed")
        self.observe(batch, "submitted", ["song-1"], "Playback completed")
        self.assertEqual(self.manifest()["batches"][0]["evidence"][-1]["text"], "Playback completed")

    def test_import_existing_result_preserves_pending_and_blocks_new_generation(self):
        pending = self.observe(self.batch(), "uncertain", evidence="Old unresolved attempt")
        self.assertTrue(hasattr(self.p, "import_existing_batch"))
        observation = self.json_file("existing.json", dict(
            status="submitted", song_ids=["existing-song"], evidence="Previously completed result receipt"))
        imported = self.p.import_existing_batch(self.root, self.package(), self.auth(), observation)
        self.assertEqual(imported["submission_status"], "submitted")
        self.assertEqual(imported["origin"], "existing_result")
        self.assertEqual(self.manifest()["batches"][0], pending)
        again = self.p.import_existing_batch(self.root, self.package(), self.auth(), observation)
        self.assertEqual(again["id"], imported["id"])
        self.assertEqual(len(self.manifest()["batches"]), 2)
        with self.assertRaises(self.p.ProjectError):
            self.batch()
        with self.assertRaises(self.p.ProjectError):
            self.observe(imported, "uncertain")
        self.assertEqual(self.p.project_status(self.root)["next_action"], "reconcile_suno")

    def test_import_existing_result_rejects_uncertain_and_conflicting_receipts(self):
        self.assertTrue(hasattr(self.p, "import_existing_batch"))
        observation = self.json_file("existing.json", dict(
            status="uncertain", song_ids=[], evidence="Result is not known"))
        with self.assertRaises(self.p.ProjectError):
            self.p.import_existing_batch(self.root, self.package(), self.auth(), observation)
        observation = self.json_file("existing.json", dict(
            status="submitted", song_ids=["existing-song"], evidence="Previously observed receipt"))
        self.p.import_existing_batch(self.root, self.package(), self.auth(), observation)
        with self.assertRaises(self.p.ProjectError):
            self.p.import_existing_batch(self.root, self.package(style="Different package"), self.auth(), observation)
        self.assertEqual(len(self.manifest()["batches"]), 1)

    def test_terminal_submission_cannot_be_reopened(self):
        batch = self.batch()
        self.observe(batch, "uncertain")
        self.observe(batch, "not_submitted")
        with self.assertRaises(self.p.ProjectError):
            self.observe(batch, "uncertain")
        self.assertEqual(self.manifest()["batches"][0]["submission_status"], "not_submitted")


class SkillDeliveryTest(BatchCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(hasattr(self.p, "add_candidate"), "Candidate delivery is not implemented yet")

    def submitted(self, ids=None):
        batch = self.batch()
        self.observe(batch, "uncertain")
        return self.observe(batch, "submitted", ids or ["song-1", "song-2"])

    def wav(self, name="candidate.wav", amplitude=0):
        path = self.base / name
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(amplitude.to_bytes(2, "little", signed=True) * 1600)
        return path

    def mp3(self):
        # Structural fixture, not an audible or fully decoded MP3 claim.
        path = self.base / "candidate.mp3"
        path.write_bytes((b"\xff\xfb\x90\x00" + b"\0" * 413) * 2)
        return path

    def candidate(self, batch, song="song-1", amplitude=0):
        return self.p.add_candidate(self.root, batch["id"], song, self.wav(amplitude=amplitude),
                                    "https://suno.com/song/" + song)

    def select(self, ids):
        return self.p.record_review(self.root, self.json_file("decision.json",
                                     dict(notes="User listened and selected", selected_ids=ids)))

    def test_same_names_and_batch_numbers_remain_distinct(self):
        first = self.candidate(self.submitted(["song-1"]))
        second = self.candidate(self.submitted(["song-2"]), "song-2", 1)
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["path"], second["path"])
        self.assertNotEqual((self.root / first["path"]).read_bytes(), (self.root / second["path"]).read_bytes())

    def test_clip_identity_is_idempotent(self):
        batch = self.submitted()
        first = self.candidate(batch)
        self.assertEqual(self.candidate(batch)["id"], first["id"])
        with self.assertRaises(self.p.ProjectError):
            self.candidate(batch, amplitude=1)
        other = self.submitted(["song-1"])
        with self.assertRaises(self.p.ProjectError):
            self.candidate(other)
        self.assertEqual(len(self.manifest()["candidates"]), 1)

    def test_same_song_dual_format_import_and_archive(self):
        batch = self.submitted()
        wav = self.candidate(batch)
        try:
            mp3 = self.p.add_candidate(self.root, batch["id"], "song-1", self.mp3(),
                                       "https://suno.com/song/song-1")
        except self.p.ProjectError as exc:
            self.fail("A second format of the same song must be accepted: " + str(exc))
        self.assertNotEqual(wav["id"], mp3["id"])
        self.assertEqual(self.candidate(batch)["id"], wav["id"])
        self.p.record_review(self.root, self.json_file("decision.json", dict(
            notes="Synthetic dual-format request", selected_ids=[wav["id"], mp3["id"]],
            required_formats=["mp3", "wav"])))
        archived = self.p.archive_project(self.root)
        folder = self.root / archived["path"]
        self.assertEqual((folder / wav["path"]).read_bytes(), (self.root / wav["path"]).read_bytes())
        self.assertEqual((folder / mp3["path"]).read_bytes(), (self.root / mp3["path"]).read_bytes())
        self.assertEqual(self.p.project_status(self.root)["delivery_status"], "archived")
        (self.root / mp3["path"]).write_bytes(b"changed")
        self.assertEqual(self.p.project_status(self.root)["next_action"], "repair_files")

    def test_required_formats_block_incomplete_archive_and_survive_resume(self):
        candidate = self.candidate(self.submitted())
        try:
            self.p.record_review(self.root, self.json_file("decision.json", dict(
                notes="Need both formats", selected_ids=[candidate["id"]], required_formats=["mp3", "wav"])))
        except self.p.ProjectError as exc:
            self.fail("Review must retain explicit format requirements: " + str(exc))
        self.p.record_review(self.root, self.json_file("note.json", dict(notes="Additional note")))
        out = self.cli("status", "--project", self.root)
        state = json.loads(out.stdout)
        self.assertEqual(state["required_formats"], ["mp3", "wav"])
        self.assertEqual(state["delivery_status"], "missing_formats")
        with self.assertRaises(self.p.ProjectError) as caught:
            self.p.archive_project(self.root)
        self.assertEqual(caught.exception.code, "formats")
        self.assertEqual(self.manifest()["archives"], [])

    def test_changed_format_requirement_invalidates_old_archive_status(self):
        candidate = self.candidate(self.submitted())
        self.select([candidate["id"]])
        self.p.archive_project(self.root)
        self.p.record_review(self.root, self.json_file("decision.json", dict(
            notes="New dual-format requirement", required_formats=["mp3", "wav"])))
        self.assertEqual(self.p.project_status(self.root)["delivery_status"], "missing_formats")

    def test_invalid_format_requirement_preserves_state(self):
        before = (self.root / "project.json").read_bytes()
        for value in ("mp3", ["flac"], [None], [["mp3"]]):
            with self.subTest(value=value), self.assertRaises(self.p.ProjectError):
                self.p.record_review(self.root, self.json_file("decision.json", dict(
                    notes="Invalid format fixture", required_formats=value)))
            self.assertEqual((self.root / "project.json").read_bytes(), before)

    def test_format_requirements_apply_to_each_selected_song(self):
        batch = self.submitted()
        first, second = self.candidate(batch), self.candidate(batch, "song-2")
        mp3 = self.p.add_candidate(self.root, batch["id"], "song-2", self.mp3(),
                                   "https://suno.com/song/song-2")
        self.p.record_review(self.root, self.json_file("decision.json", dict(
            notes="Both songs need both formats", selected_ids=[first["id"], second["id"], mp3["id"]],
            required_formats=["mp3", "wav"])))
        with self.assertRaises(self.p.ProjectError) as caught:
            self.p.archive_project(self.root)
        self.assertEqual(caught.exception.code, "formats")

    def test_import_requires_observed_song(self):
        batch = self.submitted()
        with self.assertRaises(self.p.ProjectError):
            self.candidate(batch, "unobserved")
        self.assertEqual(self.manifest()["candidates"], [])

    def test_source_identity_matches_song_on_import_and_archive(self):
        batch = self.submitted()
        with self.assertRaises(self.p.ProjectError):
            self.p.add_candidate(self.root, batch["id"], "song-1", self.wav(), "https://suno.com/song/song-2")
        candidate = self.candidate(batch)
        self.select([candidate["id"]])
        state = self.manifest()
        state["candidates"][0]["source_page"] = "https://suno.com/song/song-2"
        (self.root / "project.json").write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(self.p.ProjectError):
            self.p.archive_project(self.root)
        self.assertEqual(self.manifest()["archives"], [])

    def test_fake_or_missing_audio_blocked(self):
        batch = self.submitted()
        for name, content in (("fake.mp3", "<html>" * 500), ("fake.wav", "RIFF" * 500),
                              ("tag.mp3", "ID3" + "x" * 3000)):
            path = self.text(name, content)
            with self.subTest(name=name), self.assertRaises(self.p.ProjectError):
                self.p.add_candidate(self.root, batch["id"], "song-1", path, "https://suno.com/song/song-1")
        with self.assertRaises(self.p.ProjectError):
            self.p.add_candidate(self.root, batch["id"], "song-1", self.base / "missing.mp3",
                                 "https://suno.com/song/song-1")

    def test_review_requires_explicit_selection(self):
        self.candidate(self.submitted())
        self.p.record_review(self.root, self.json_file("decision.json", dict(notes="Nice chorus")))
        self.assertEqual(self.manifest()["final_selection"], [])
        with self.assertRaises(self.p.ProjectError):
            self.p.archive_project(self.root)
        with self.assertRaises(self.p.ProjectError):
            self.select(["unknown"])

    def test_manifest_size_limit_preserves_readable_state(self):
        review = self.json_file("large-review.json", dict(notes="x" * 1100000))
        self.p.record_review(self.root, review)
        before = (self.root / "project.json").read_bytes()
        with self.assertRaises(self.p.ProjectError):
            self.p.record_review(self.root, review)
        self.assertEqual((self.root / "project.json").read_bytes(), before)
        self.assertEqual(self.p.project_status(self.root)["problems"], [])

    def test_archive_contains_only_selected_material(self):
        batch = self.submitted()
        first, second = self.candidate(batch), self.candidate(batch, "song-2", 1)
        self.select([first["id"]])
        result = self.p.archive_project(self.root)
        folder = self.root / result["path"]
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["selected_ids"], [first["id"]])
        self.assertEqual(len(list(folder.rglob("*.wav"))), 1)
        self.assertEqual(list(folder.rglob("*.wav"))[0].read_bytes(), (self.root / first["path"]).read_bytes())
        self.assertNotIn(second["id"], (folder / "manifest.json").read_text())
        self.assertTrue((folder / "review.md").is_file())

    def test_tampered_audio_or_package_blocks_archive(self):
        batch = self.submitted()
        candidate = self.candidate(batch)
        self.select([candidate["id"]])
        for relative in (candidate["path"], batch["package_path"]):
            path = self.root / relative
            old = path.read_bytes()
            path.write_bytes(b"corrupted")
            with self.subTest(relative=relative), self.assertRaises(self.p.ProjectError):
                self.p.archive_project(self.root)
            path.write_bytes(old)
        self.assertEqual(self.manifest()["archives"], [])

    def test_archive_rejects_mismatched_lyric_provenance(self):
        candidate = self.candidate(self.submitted())
        self.select([candidate["id"]])
        other = self.p.add_lyrics(self.root, self.text("other.txt", "Different approved lyrics"))
        self.p.approve_lyrics(self.root, other["id"], self.text("approval.txt", "Synthetic approval"))
        for reference in (other["id"], None):
            state = self.manifest()
            state["batches"][0]["lyrics_id"] = reference
            (self.root / "project.json").write_text(json.dumps(state), encoding="utf-8")
            with self.subTest(reference=reference), self.assertRaises(self.p.ProjectError):
                self.p.archive_project(self.root)
        self.assertEqual(self.manifest()["archives"], [])

    def test_archive_does_not_duplicate_candidates(self):
        candidate = self.candidate(self.submitted())
        self.select([candidate["id"], candidate["id"]])
        self.p.archive_project(self.root)
        self.assertEqual(len(self.manifest()["candidates"]), 1)
        self.assertEqual(self.manifest()["final_selection"], [candidate["id"]])

    def test_status_checks_exported_files_and_manifest(self):
        candidate = self.candidate(self.submitted())
        self.select([candidate["id"]])
        archived = self.p.archive_project(self.root)
        folder = self.root / archived["path"]
        for relative in (candidate["path"], "manifest.json", "review.md"):
            path = folder / relative
            before = path.read_bytes()
            for missing in (False, True):
                path.write_bytes(b"changed")
                if missing:
                    path.unlink()
                status = self.p.project_status(self.root)
                with self.subTest(relative=relative, missing=missing):
                    self.assertEqual(status["next_action"], "repair_files")
                    self.assertTrue(status["problems"])
                path.write_bytes(before)
        self.assertEqual(self.p.project_status(self.root)["problems"], [])

    def test_archive_failure_keeps_previous_output(self):
        candidate = self.candidate(self.submitted())
        self.select([candidate["id"]])
        first = self.p.archive_project(self.root)
        before = (self.root / first["path"] / "manifest.json").read_bytes()
        with patch.object(self.p.shutil, "copyfile", side_effect=OSError("disk error")):
            with self.assertRaises(self.p.ProjectError):
                self.p.archive_project(self.root)
        self.assertEqual((self.root / first["path"] / "manifest.json").read_bytes(), before)
        self.assertEqual(len(self.manifest()["archives"]), 1)

    def test_archive_recovers_from_transient_windows_rename_denial(self):
        candidate = self.candidate(self.submitted())
        self.select([candidate["id"]])
        real_rename = self.p.os.rename
        attempts = []

        def briefly_locked(source, target):
            attempts.append((source, target))
            if len(attempts) < 3:
                raise PermissionError(13, "Synthetic sharing violation", None, 32)
            return real_rename(source, target)

        with patch.object(self.p.sys, "platform", "win32"), patch.object(self.p.os, "rename", side_effect=briefly_locked):
            try:
                archived = self.p.archive_project(self.root)
            except self.p.ProjectError as exc:
                self.fail("Temporary archive directory contention should recover: " + str(exc))
        self.assertEqual(len(attempts), 3)
        self.assertEqual((self.root / archived["path"] / candidate["path"]).read_bytes(),
                         (self.root / candidate["path"]).read_bytes())
        self.assertEqual(self.p.project_status(self.root)["delivery_status"], "archived")

    def test_archive_persistent_windows_denial_preserves_previous_output(self):
        candidate = self.candidate(self.submitted())
        self.select([candidate["id"]])
        first = self.p.archive_project(self.root)
        before = (self.root / first["path"] / "manifest.json").read_bytes()
        denied = PermissionError(13, "Synthetic persistent directory denial", None, 5)
        with patch.object(self.p.sys, "platform", "win32"), patch.object(self.p.os, "rename", side_effect=denied) as operation:
            with self.assertRaises(self.p.ProjectError) as caught:
                self.p.archive_project(self.root)
        self.assertLessEqual(operation.call_count, 5)
        self.assertTrue(caught.exception.residual_paths[0].startswith("final/.pending-"))
        self.assertEqual((self.root / first["path"] / "manifest.json").read_bytes(), before)
        self.assertEqual(len(self.manifest()["archives"]), 1)

    def test_source_page_excludes_credentials_and_signed_queries(self):
        batch = self.submitted()
        for url in ("https://user:pass@suno.com/song/song-1", "https://suno.com/song/song-1?token=fake",
                    "https://suno.com/song/song-1#private", "https://evil.example/song/song-1",
                    "https://suno.com/audio.mp3"):
            with self.subTest(url=url), self.assertRaises(self.p.ProjectError):
                self.p.add_candidate(self.root, batch["id"], "song-1", self.wav(), url)
        self.assertEqual(self.manifest()["candidates"], [])

    def test_archive_failure_reports_residual_location(self):
        candidate = self.candidate(self.submitted())
        self.select([candidate["id"]])
        for operation in ("copy", "save"):
            failure = patch.object(self.p.shutil, "copyfile", side_effect=OSError("disk error")) if operation == "copy" else patch.object(self.p.os, "replace", side_effect=OSError("disk error"))
            with self.subTest(operation=operation), failure:
                with self.assertRaises(self.p.ProjectError) as caught:
                    self.p.archive_project(self.root)
                residuals = getattr(caught.exception, "residual_paths", [])
                self.assertEqual(len(residuals), 1)
                self.assertTrue((self.root / residuals[0]).is_dir())
                self.assertTrue(residuals[0].startswith("final/.pending-") if operation == "copy" else
                                residuals[0].startswith("final/") and ".pending-" not in residuals[0])
            self.assertEqual(self.manifest()["archives"], [])


class SkillDistributionTest(SkillCase):
    def setUp(self):
        super().setUp()
        self.installed = self.base / "installed" / "ai-music-workflow"
        for relative in DELIVERY_FILES:
            source = SKILL / relative
            self.assertTrue(source.is_file(), "Missing delivery file: " + relative)
            target = self.installed / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        self.before = self.hashes()

    def hashes(self):
        return {p.relative_to(self.installed).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.installed.rglob("*") if p.is_file()}

    def run_cli(self, command, *args, cwd=None):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, "-I", "-S", str(self.installed / "scripts/project.py"),
                                 command, "--project", str(self.root), *map(str, args)],
                                cwd=cwd or self.base, env=env, capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def full_flow(self, brief=None):
        self.run_cli("init", "--title", "Standalone test", "--brief-file",
                     brief if brief is not None else self.text("brief.txt", "Synthetic only"))
        lyrics = LYRICS + "\n[Chorus]\n\u6211\u4eec\u56de\u5bb6\n"
        lyric = self.run_cli("add-lyrics", "--file", self.text("lyrics.txt", lyrics))
        self.run_cli("approve-lyrics", "--lyrics-id", lyric["id"], "--approval-file",
                     self.text("approval.txt", "Synthetic test approval, not a real user decision"))
        package = self.json_file("package.json", dict(title="Test", style="Acoustic", lyrics_id=lyric["id"],
                                                      lyrics=lyrics.replace("\n", "\r\n"), instrumental=False))
        auth = self.json_file("auth.json", dict(approval="Synthetic one-click authorization", max_generation_clicks=1))
        batch = self.run_cli("prepare-batch", "--package-file", package, "--authorization-file", auth)
        observation = self.json_file("observation.json", dict(status="uncertain", song_ids=[], evidence="Synthetic"))
        self.run_cli("record-submission", "--batch-id", batch["id"], "--observation-file", observation)
        self.assertEqual(self.run_cli("status")["next_action"], "reconcile_suno")
        observation = self.json_file("observation.json", dict(status="submitted", song_ids=["synthetic-1"], evidence="Synthetic"))
        self.run_cli("record-submission", "--batch-id", batch["id"], "--observation-file", observation)
        audio = self.base / "synthetic.wav"
        with wave.open(str(audio), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\0\0" * 1600)
        candidate = self.run_cli("add-candidate", "--batch-id", batch["id"], "--song-id", "synthetic-1", "--file", audio,
                                 "--source-page", "https://suno.com/song/synthetic-1")
        mp3_file = self.base / "synthetic.mp3"
        mp3_file.write_bytes((b"\xff\xfb\x90\x00" + b"\0" * 413) * 2)
        mp3 = self.run_cli("add-candidate", "--batch-id", batch["id"], "--song-id", "synthetic-1", "--file", mp3_file,
                          "--source-page", "https://suno.com/song/synthetic-1")
        self.run_cli("review", "--decision-file", self.json_file("decision.json", dict(
            notes="Synthetic selection, no listening claim", selected_ids=[candidate["id"], mp3["id"]],
            required_formats=["mp3", "wav"])))
        archive = self.run_cli("archive")
        result = json.loads((self.root / archive["path"] / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(result["selected_ids"], [candidate["id"], mp3["id"]])
        self.assertEqual((self.root / archive["path"] / mp3["path"]).read_bytes(), mp3_file.read_bytes())
        self.assertEqual((self.root / archive["path"] / lyric["path"]).read_text(encoding="utf-8"), lyrics)
        self.assertEqual(len(self.run_cli("status")["candidates"]), 2)
        self.assertEqual(self.run_cli("status")["delivery_status"], "archived")
        return archive

    def test_imported_delivery_resumes_without_unlocking_legacy_generation(self):
        self.full_flow()
        state = self.manifest()
        package = self.root / state["batches"][0]["package_path"]
        auth = self.json_file("import-auth.json", state["batches"][0]["authorization"])
        pending = self.run_cli("prepare-batch", "--package-file", package, "--authorization-file", auth)
        self.run_cli("record-submission", "--batch-id", pending["id"], "--observation-file",
                     self.json_file("pending.json", dict(status="uncertain", song_ids=[], evidence="Unresolved older test")))
        observation = self.json_file("existing.json", dict(status="submitted", song_ids=["existing-result"],
                                     evidence="Synthetic existing result receipt; no new generation"))
        batch = self.run_cli("import-existing-batch", "--package-file", package,
                             "--authorization-file", auth, "--observation-file", observation)
        selected = []
        for extension in ("mp3", "wav"):
            row = self.run_cli("add-candidate", "--batch-id", batch["id"], "--song-id", "existing-result",
                               "--file", self.base / ("synthetic." + extension),
                               "--source-page", "https://suno.com/song/existing-result")
            selected.append(row["id"])
        self.run_cli("review", "--decision-file", self.json_file("new-selection.json", dict(
            notes="Synthetic selected existing result", selected_ids=selected, required_formats=["mp3", "wav"])))
        self.run_cli("archive")
        status = self.run_cli("status")
        self.assertEqual(status["next_action"], "reconcile_suno")
        self.assertEqual(status["delivery_status"], "archived")
        self.assertEqual(status["final_selection"], selected)
        blocked = self.cli("prepare-batch", "--project", self.root, "--package-file", package,
                           "--authorization-file", auth, script=self.installed / "scripts/project.py")
        self.assertEqual(blocked.returncode, 2)
        self.assertEqual(json.loads(blocked.stderr)["error"], "pending")
        self.assertEqual(self.hashes(), self.before)

    def test_package_runs_without_application(self):
        self.full_flow()
        self.assertFalse((self.base / "ai_music_workflow").exists())

    def test_user_chosen_library_supports_unicode_spaces_and_resume(self):
        library = self.base / "\u7528\u6237\u7684\u6b4c\u66f2 My Songs"
        library.mkdir()
        marker = library / "existing-notes.txt"
        marker.write_text("Existing user material", encoding="utf-8")
        self.root = library / "\u661f\u5149 Song A"
        inputs = library / ".request-inputs"
        inputs.mkdir()
        brief = inputs / "brief.md"
        brief.write_text("Synthetic new-user brief", encoding="utf-8")
        self.assertFalse(self.root.exists())
        archive = self.full_flow(brief=brief)
        self.assertEqual((self.root / "brief.md").read_bytes(), brief.read_bytes())
        manifest = (self.root / "project.json").read_bytes()
        status = self.run_cli("status", cwd=self.installed)
        self.assertEqual(status["project_id"], json.loads(manifest)["project_id"])
        self.assertEqual(status["delivery_status"], "archived")
        self.assertTrue((self.root / archive["path"]).is_relative_to(library))
        self.assertEqual((self.root / "project.json").read_bytes(), manifest)
        self.assertEqual(marker.read_text(encoding="utf-8"), "Existing user material")
        self.assertFalse((library / "project.json").exists())
        self.assertFalse((self.base / "project.json").exists())
        self.assertEqual(self.hashes(), self.before)

    def test_cli_requires_explicit_project_without_writing_to_cwd(self):
        brief = self.text("brief.txt", "Synthetic new-user request")
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(self.installed / "scripts/project.py"),
             "init", "--title", "New song", "--brief-file", str(brief)],
            cwd=self.installed, capture_output=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["error"], "usage")
        self.assertFalse((self.installed / "project.json").exists())
        self.assertEqual(self.hashes(), self.before)

    def test_distribution_allowlist(self):
        path = self.base / "skill.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for relative in DELIVERY_FILES:
                archive.write(self.installed / relative, "ai-music-workflow/" + relative)
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(set(archive.namelist()), {"ai-music-workflow/" + p for p in DELIVERY_FILES})
            self.assertIsNone(archive.testzip())

    def test_preflight_is_read_only_without_a_project(self):
        self.text("existing-notes.txt", "Preserve user material")

        def snapshot():
            return {p.relative_to(self.base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.base.rglob("*") if p.is_file()}

        before = snapshot()
        commands = (
            [sys.executable, "-I", "-S", "--version"],
            [sys.executable, "-I", "-S", str(self.installed / "scripts/project.py"), "--help"],
        )
        for command in commands:
            result = subprocess.run(command, cwd=self.base, capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.strip())
        self.assertFalse(self.root.exists())
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.hashes(), self.before)

    def test_runtime_stays_outside_skill(self):
        self.full_flow()
        self.assertEqual(self.hashes(), self.before)
        self.assertEqual(set(self.hashes()), set(DELIVERY_FILES))


if __name__ == "__main__":
    unittest.main()
