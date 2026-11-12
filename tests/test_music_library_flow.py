import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile
import wave
import unittest
from unittest.mock import patch

from test_producer_library import LibraryCase
from test_music_workflow_skill import DELIVERY_FILES, SKILL


class FlowCase(LibraryCase):
    def song(self, name="song"):
        root = self.base / name
        self.p.init_project(root, name, self.text("brief.txt", "Synthetic song"))
        return root

    def package(self):
        return self.json_file("package.json", dict(title="Song", style="Short repeating chorus motif, bright pop",
                              lyrics_id=None, lyrics="", instrumental=True))

    def auth(self):
        return self.json_file("auth.json", dict(approval="One synthetic test", max_generation_clicks=1))

    def selection(self, item):
        return self.json_file("selection.json", dict(library_id=self.state()["library_id"],
            approval="Use selected reference for this song", items=[dict(entry_id=item["id"], revision=item["revision"],
                sha256=item["sha256"], summary="PRIVATE-SUMMARY marker", usage="Short repeating chorus motif")]))

    def observe(self, root, batch, status, **kwargs):
        return self.p.record_submission(root, batch["id"], self.json_file("observation.json",
            dict(status=status, song_ids=["synthetic-song"] if status == "submitted" else [], evidence="Synthetic observation")), **kwargs)

    def archive(self, root, batch):
        self.observe(root, batch, "uncertain", library_root=self.root if batch.get("reference_id") else None)
        self.observe(root, batch, "submitted")
        audio = self.base / "audio.wav"
        with wave.open(str(audio), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\0\0" * 800)
        candidate = self.p.add_candidate(root, batch["id"], "synthetic-song", audio, "https://suno.com/song/synthetic-song")
        self.p.record_review(root, self.json_file("decision.json", dict(notes="Synthetic review", selected_ids=[candidate["id"]])))
        return self.p.archive_project(root)


class ProjectLibraryTest(FlowCase):
    def test_corrupt_reference_events_is_controlled_schema_error(self):
        root = self.song()
        path = root / "project.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        for events in (None, {}, "bad", [None], [{"action": "clear"}],
                       [dict(action="clear", at="now", reason="reason", reference_id="unknown")]):
            with self.subTest(events=events):
                state["reference_events"] = events
                path.write_text(json.dumps(state), encoding="utf-8")
                before = path.read_bytes()
                with self.assertRaises(self.p.ProjectError) as caught:
                    self.p.clear_references(root, self.text("reason.md", "Clear reference"))
                self.assertEqual(caught.exception.code, "schema")
                self.assertEqual(path.read_bytes(), before)
                from test_producer_library import SCRIPTS
                out = subprocess.run([sys.executable, "-I", "-S", str(SCRIPTS / "project.py"), "status", "--project", str(root)],
                                     capture_output=True, text=True, timeout=15)
                self.assertEqual(out.returncode, 2)
                self.assertEqual(json.loads(out.stderr)["error"], "schema")

    def test_reference_save_failure_reports_residual_snapshot(self):
        item = self.draft()
        self.approve(item)
        root = self.song()
        before = (root / "project.json").read_bytes()
        selection = self.selection(item)
        for mode in ("manifest", "partial_snapshot"):
            original = self.p._write_new
            def partial(root, relative, text):
                original(root, relative, text[:12])
                raise OSError("synthetic partial write")
            injected = patch.object(self.p._io.os, "replace", side_effect=OSError("synthetic disk failure")) if mode == "manifest" else patch.object(self.p, "_write_new", side_effect=partial)
            with injected, self.assertRaises(self.p.ProjectError) as caught:
                self.p.attach_references(root, self.root, selection)
            self.assertEqual((root / "project.json").read_bytes(), before)
            self.assertEqual(len(caught.exception.residual_paths), 1)
            self.assertTrue((root / caught.exception.residual_paths[0]).is_file())

    def test_project_cannot_be_created_inside_library(self):
        for root in (self.root / "song", self.root / "nested/song"):
            with self.assertRaises(self.p.ProjectError) as caught:
                self.p.init_project(root, "Song", self.text("brief.md", "Brief"))
            self.assertEqual(caught.exception.code, "path")
            self.assertFalse(root.exists())
        sibling = self.song("legal-sibling")
        self.assertTrue((sibling / "project.json").is_file())

    @unittest.skipUnless(sys.platform == "win32", "Windows junction containment")
    def test_project_junction_into_library_is_rejected(self):
        alias = self.base / "library-alias"
        out = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(self.root)], capture_output=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        with self.assertRaises(self.p.ProjectError):
            self.p.init_project(alias / "song", "Song", self.text("brief.md", "Brief"))
        self.assertFalse((self.root / "song").exists())

    def test_two_projects_keep_exact_reference_revision(self):
        item = self.draft()
        self.approve(item)
        for name in ("song-a", "song-b"):
            root = self.song(name)
            ref = self.p.attach_references(root, self.root, self.selection(item))
            batch = self.p.prepare_batch(root, self.package(), self.auth(), library_root=self.root)
            self.assertEqual(batch["reference_id"], ref["id"])
            saved = json.loads((root / ref["path"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["selection"]["items"][0]["revision"], 1)
            self.assertEqual(saved["selection"]["items"][0]["sha256"], item["sha256"])

    def test_revoke_blocks_prepared_batch_without_rewriting_history(self):
        item = self.draft()
        self.approve(item)
        root = self.song()
        ref = self.p.attach_references(root, self.root, self.selection(item))
        batch = self.p.prepare_batch(root, self.package(), self.auth(), library_root=self.root)
        before = (root / batch["package_path"]).read_bytes()
        self.assertTrue(self.p.check_generation(root, batch["id"], self.root)["ready"])
        self.lib.revoke_entry(self.root, item["id"], self.text("reason.md", "Withdraw"))
        self.p.clear_references(root, self.text("clear.md", "Future songs only"))
        for action in (lambda: self.p.check_generation(root, batch["id"], self.root),
                       lambda: self.observe(root, batch, "uncertain", library_root=self.root)):
            with self.assertRaises(self.p.ProjectError):
                action()
        self.assertEqual((root / batch["package_path"]).read_bytes(), before)
        self.assertTrue((root / ref["path"]).exists())
        self.assertEqual(self.p.project_status(root, library_root=self.root)["reference_status"], "invalid")

    def test_wrong_library_id_or_missing_library_blocks_new_use(self):
        item = self.draft()
        self.approve(item)
        root = self.song()
        self.p.attach_references(root, self.root, self.selection(item))
        other = self.base / "other-library"
        self.lib.init_library(other)
        for library in (None, other):
            with self.assertRaises(self.p.ProjectError):
                self.p.prepare_batch(root, self.package(), self.auth(), library_root=library)
        self.assertEqual(self.p.project_status(root)["reference_status"], "unverified")

    def test_legacy_no_library_flow_and_uncertain_are_unchanged(self):
        root = self.song()
        batch = self.p.prepare_batch(root, self.package(), self.auth())
        self.assertTrue(self.p.check_generation(root, batch["id"])["ready"])
        self.observe(root, batch, "uncertain")
        with self.assertRaises(self.p.ProjectError):
            self.p.check_generation(root, batch["id"])
        self.assertEqual(self.p.project_status(root)["next_action"], "reconcile_suno")

    def test_archive_excludes_private_note_and_attachment(self):
        item = self.lib.add_entry(self.root, self.json_file("card.json", self.card()),
                                  self.text("note.md", "PRIVATE-NOTE marker"), self.text("source.txt", "PRIVATE-SOURCE marker"))
        self.approve(item)
        root = self.song()
        self.p.attach_references(root, self.root, self.selection(item))
        batch = self.p.prepare_batch(root, self.package(), self.auth(), library_root=self.root)
        archive = self.archive(root, batch)
        files = list((root / archive["path"]).rglob("*"))
        blob = b"".join(p.read_bytes() for p in files if p.is_file())
        for value in (b"PRIVATE-NOTE", b"PRIVATE-SOURCE", b"PRIVATE-SUMMARY"):
            self.assertNotIn(value, blob)
        self.assertIn(item["id"].encode(), blob)
        self.assertTrue((root / archive["path"] / "provenance.json").is_file())

    def test_local_only_reference_blocks_generation_but_allows_writing(self):
        item = self.draft(permissions=dict(assistant_read=True, reuse=True, external_generation=False))
        self.approve(item)
        root = self.song()
        self.p.attach_references(root, self.root, self.selection(item))
        with self.assertRaises(self.p.ProjectError):
            self.p.prepare_batch(root, self.package(), self.auth(), library_root=self.root)

    def test_reference_tampering_reports_repair_files(self):
        item = self.draft()
        self.approve(item)
        root = self.song()
        ref = self.p.attach_references(root, self.root, self.selection(item))
        (root / ref["path"]).write_text("{}")
        self.assertEqual(self.p.project_status(root)["next_action"], "repair_files")
        with self.assertRaises(self.p.ProjectError):
            self.p.prepare_batch(root, self.package(), self.auth(), library_root=self.root)

    def test_historical_import_does_not_acquire_current_references(self):
        item = self.draft()
        self.approve(item)
        root = self.song()
        self.p.attach_references(root, self.root, self.selection(item))
        obs = self.json_file("existing.json", dict(status="submitted", song_ids=["existing"], evidence="Observed previous result"))
        batch = self.p.import_existing_batch(root, self.package(), self.auth(), obs)
        self.assertIsNone(batch.get("reference_id"))


class LessonTest(FlowCase):
    def archived_song(self):
        root = self.song()
        batch = self.p.prepare_batch(root, self.package(), self.auth())
        return root, self.archive(root, batch)

    def lesson(self, root, archive_id):
        return self.lib.draft_lesson(self.root, root, archive_id,
            self.json_file("card.json", self.card(kind="lesson")),
            self.text("lesson.md", "Try a short repeating chorus motif when the song should feel light."))

    def test_lesson_is_draft_until_explicit_approval(self):
        root, archive = self.archived_song()
        item = self.lesson(root, archive["id"])
        self.assertEqual(self.query()["items"], [])
        self.approve(item)
        result = self.query()["items"][0]
        self.assertEqual(result["kind"], "lesson")
        self.assertEqual(result["source"]["archive_id"], archive["id"])
        self.assertEqual(result["source"]["manifest_sha256"], archive["manifest_sha256"])

    def test_fake_archive_or_tampered_review_rejected(self):
        root, archive = self.archived_song()
        with self.assertRaises(self.lib.LibraryError):
            self.lesson(root, "invented-archive")
        (root / archive["path"] / "review.md").write_text("Changed review")
        with self.assertRaises(self.lib.LibraryError):
            self.lesson(root, archive["id"])
        self.assertEqual(self.state()["entries"], [])

    def test_learning_does_not_mutate_source_or_unlock_pending(self):
        root, archive = self.archived_song()
        batch = self.p.prepare_batch(root, self.package(), self.auth())
        self.observe(root, batch, "uncertain")
        before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
        self.lesson(root, archive["id"])
        self.assertEqual({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}, before)
        self.assertEqual(self.p.project_status(root)["next_action"], "reconcile_suno")


class DistributionFlowTest(FlowCase):
    def test_two_song_library_roundtrip_from_extracted_zip(self):
        archive = self.base / "candidate.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            for relative in DELIVERY_FILES:
                handle.write(SKILL / relative, "ai-music-workflow/" + relative)
        extracted = self.base / "extracted"
        with zipfile.ZipFile(archive) as handle:
            self.assertCountEqual(handle.namelist(), ["ai-music-workflow/" + path for path in DELIVERY_FILES])
            handle.extractall(extracted)
        skill = extracted / "ai-music-workflow"
        scripts = skill / "scripts"
        before_skill = {str(p.relative_to(skill)): p.read_bytes() for p in skill.rglob("*") if p.is_file()}
        def cli(which, *args, success=True):
            out = subprocess.run([sys.executable, "-I", "-S", str(scripts / (which + ".py")), *map(str, args)],
                                 cwd=self.base, capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(out.returncode, 0 if success else 2, out.stderr)
            return json.loads(out.stdout if success else out.stderr)
        library = self.base / "\u7d20\u6750 \u5e93"
        lib = cli("library", "init", "--library", library)
        a, b = self.base / "\u6b4c\u66f2 A", self.base / "\u6b4c\u66f2 B"
        for root in (a, b):
            cli("project", "init", "--project", root, "--title", root.name, "--brief-file", self.text("brief.txt", "Synthetic light pop"))
        batch_a = cli("project", "prepare-batch", "--project", a, "--package-file", self.package(), "--authorization-file", self.auth())
        for status in ("uncertain", "submitted"):
            obs = self.json_file("obs.json", dict(status=status, song_ids=["song-a"] if status == "submitted" else [], evidence="Synthetic receipt"))
            cli("project", "record-submission", "--project", a, "--batch-id", batch_a["id"], "--observation-file", obs)
        audio = self.base / "final.wav"
        with wave.open(str(audio), "wb") as h:
            h.setnchannels(1)
            h.setsampwidth(2)
            h.setframerate(8000)
            h.writeframes(b"\0\0" * 800)
        candidate = cli("project", "add-candidate", "--project", a, "--batch-id", batch_a["id"], "--song-id", "song-a",
                        "--source-page", "https://suno.com/song/song-a", "--file", audio)
        cli("project", "review", "--project", a, "--decision-file", self.json_file("decision.json",
                dict(notes="Synthetic favorable review of short chorus", selected_ids=[candidate["id"]])))
        saved_a = cli("project", "archive", "--project", a)
        before_a = {str(p.relative_to(a)): p.read_bytes() for p in a.rglob("*") if p.is_file()}
        lesson = cli("library", "draft-lesson", "--library", library, "--project", a, "--archive-id", saved_a["id"],
                     "--card-file", self.json_file("card.json", self.card(kind="lesson", title="\u8f7b\u5feb\u526f\u6b4c")),
                     "--note-file", self.text("note.md", "Try a short repeating chorus motif."))
        q = self.json_file("query.json", dict(keywords=["\u526f\u6b4c"], tags=[], kinds=[], offset=0, limit=20))
        self.assertEqual(cli("library", "search", "--library", library, "--query-file", q)["items"], [])
        cli("library", "approve", "--library", library, "--entry-id", lesson["id"], "--revision", 1,
            "--approval-file", self.json_file("approval.json", dict(sha256=lesson["sha256"], approval="Synthetic producer approves scope")))
        found = cli("library", "search", "--library", library, "--query-file", q)["items"][0]
        self.assertEqual(found["source"]["archive_id"], saved_a["id"])
        selection = self.json_file("select.json", dict(library_id=lib["library_id"], approval="Use this chorus idea for B",
            items=[dict(entry_id=found["id"], revision=1, sha256=found["sha256"], summary=found["note"], usage="Repeat the short chorus motif")]))
        ref = cli("project", "attach-references", "--project", b, "--library", library, "--selection-file", selection)
        batch_b = cli("project", "prepare-batch", "--project", b, "--library", library, "--package-file", self.package(), "--authorization-file", self.auth())
        self.assertEqual(batch_b["reference_id"], ref["id"])
        package_bytes = (b / batch_b["package_path"]).read_bytes()
        self.assertIn("Short repeating chorus motif", json.loads(package_bytes)["style"])
        self.assertTrue(cli("project", "check-generation", "--project", b, "--library", library, "--batch-id", batch_b["id"])["ready"])
        moved_library, moved_b = self.base / "moved-library", self.base / "moved-song"
        shutil.copytree(library, moved_library)
        shutil.copytree(b, moved_b)
        library.rename(self.base / "offline-library")
        b.rename(self.base / "offline-song")
        self.assertTrue(cli("project", "check-generation", "--project", moved_b, "--library", moved_library, "--batch-id", batch_b["id"])["ready"])
        cli("library", "revoke", "--library", moved_library, "--entry-id", lesson["id"], "--reason-file", self.text("reason.md", "Withdraw"))
        error = cli("project", "check-generation", "--project", moved_b, "--library", moved_library, "--batch-id", batch_b["id"], success=False)
        self.assertEqual(error["error"], "approval")
        self.assertEqual((moved_b / batch_b["package_path"]).read_bytes(), package_bytes)
        self.assertEqual({str(p.relative_to(a)): p.read_bytes() for p in a.rglob("*") if p.is_file()}, before_a)
        self.assertEqual({str(p.relative_to(skill)): p.read_bytes() for p in skill.rglob("*") if p.is_file()}, before_skill)

    def test_only_allowlisted_public_files_and_no_runtime_data(self):
        self.assertEqual(len(DELIVERY_FILES), 12)
        for relative in DELIVERY_FILES:
            content = (SKILL / relative).read_text(encoding="utf-8")
            self.assertNotIn("\ufffd", content)
            self.assertNotIn(str(self.base), content)
        frontmatter = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = next(line.split('"')[1] for line in frontmatter.splitlines()
                       if line.strip().startswith("version:"))
        self.assertIn(f"`{version}`", (SKILL / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
