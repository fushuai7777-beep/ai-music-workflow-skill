import importlib.util
import json
import hashlib
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/ai-music-workflow/scripts"


def module(name):
    spec = importlib.util.spec_from_file_location("test_music_" + name, SCRIPTS / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class RecordIOTest(unittest.TestCase):
    def setUp(self):
        self.io = module("record_io")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_record_io_stays_inside_root(self):
        for relative in ("../outside", str(self.root), "x:stream"):
            with self.assertRaises(self.io.RecordError):
                self.io.inside(self.root, relative)
        self.assertEqual(self.io.inside(self.root, "notes/a.txt"), self.root / "notes/a.txt")

    def test_atomic_json_failure_preserves_old_bytes(self):
        self.io.atomic_json(self.root, "record.json", {"old": True})
        before = (self.root / "record.json").read_bytes()
        with patch.object(self.io.os, "replace", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.io.atomic_json(self.root, "record.json", {"new": True})
        self.assertEqual((self.root / "record.json").read_bytes(), before)

    def test_fixed_path_import_ignores_cwd_shadow_module(self):
        marker = self.root / "executed.txt"
        (self.root / "record_io.py").write_text("raise RuntimeError('shadow executed')", encoding="utf-8")
        out = subprocess.run([sys.executable, "-I", "-S", str(SCRIPTS / "project.py"), "--help"],
                             cwd=self.root, capture_output=True, text=True, timeout=15)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(marker.exists())


class LibraryCase(unittest.TestCase):
    def setUp(self):
        self.lib = module("library")
        self.p = module("project")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "library"
        self.lib.init_library(self.root)

    def text(self, name, content):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def json_file(self, name, value):
        return self.text(name, json.dumps(value, ensure_ascii=False))

    def card(self, **overrides):
        result = dict(kind="prompt", title="Bright chorus", tags=["pop"],
                      applies_to="Light pop", avoid_when="Quiet ballads",
                      source=dict(type="user", description="Synthetic material"),
                      permissions=dict(assistant_read=True, reuse=True, external_generation=True, share=False))
        result.update(overrides)
        return result

    def draft(self, **overrides):
        return self.lib.add_entry(self.root, self.json_file("card.json", self.card(**overrides)),
                                  self.text("note.md", "Use a short repeating chorus motif."))

    def state(self):
        return json.loads((self.root / "library.json").read_text(encoding="utf-8"))

    def approve(self, item):
        return self.lib.approve_entry(self.root, item["id"], item["revision"],
            self.json_file("approval.json", dict(sha256=item["sha256"], approval="Approved synthetic reference")))

    def query(self, **overrides):
        q = dict(keywords=[], tags=[], kinds=[], offset=0, limit=20)
        q.update(overrides)
        return self.lib.search_library(self.root, self.json_file("query.json", q))


class LibraryStorageTest(LibraryCase):
    def test_empty_entry_id_rejects_before_writing(self):
        before = (self.root / "library.json").read_bytes()
        out = subprocess.run([sys.executable, "-I", "-S", str(SCRIPTS / "library.py"), "add", "--library", str(self.root),
            "--card-file", str(self.json_file("card.json", self.card())), "--note-file", str(self.text("note.md", "Note")),
            "--entry-id", ""], capture_output=True, text=True, timeout=15)
        self.assertEqual(out.returncode, 2, out.stdout)
        self.assertNotIn("Traceback", out.stderr)
        self.assertEqual((self.root / "library.json").read_bytes(), before)
        self.assertFalse((self.root / "entries").exists())

    def test_second_process_cannot_write_while_library_is_locked(self):
        card, note = self.json_file("card.json", self.card()), self.text("note.md", "Note")
        before = (self.root / "library.json").read_bytes()
        with self.lib._locked(self.root):
            out = subprocess.run([sys.executable, "-I", "-S", str(SCRIPTS / "library.py"), "add", "--library", str(self.root),
                                  "--card-file", str(card), "--note-file", str(note)], capture_output=True, text=True, timeout=15)
            self.assertEqual(out.returncode, 2)
            self.assertEqual(json.loads(out.stderr)["error"], "locked")
            self.assertTrue((self.root / ".skill-library.lock").exists())
        self.assertEqual((self.root / "library.json").read_bytes(), before)
        self.draft()
        self.assertEqual(len(self.state()["entries"]), 1)

    @unittest.skipUnless(os.name == "nt", "Windows junction containment")
    def test_directory_junction_cannot_redirect_library_writes(self):
        outside = self.base / "outside"
        outside.mkdir()
        junction = self.root / "entries"
        out = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        with self.assertRaises(self.lib.LibraryError):
            self.draft()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.state()["entries"], [])

    def test_init_does_not_overwrite_nonempty_or_unknown_library(self):
        before = (self.root / "library.json").read_bytes()
        with self.assertRaises(self.lib.LibraryError):
            self.lib.init_library(self.root)
        self.assertEqual((self.root / "library.json").read_bytes(), before)
        other = self.base / "nonempty"
        other.mkdir()
        (other / "keep.txt").write_bytes(b"keep")
        with self.assertRaises(self.lib.LibraryError):
            self.lib.init_library(other)
        state = self.state()
        state["schema_version"] = 99
        (self.root / "library.json").write_text(json.dumps(state))
        with self.assertRaises(self.lib.LibraryError):
            self.lib.library_status(self.root)

    def test_same_name_different_bytes_and_duplicate_hash(self):
        card = self.json_file("card.json", self.card())
        note = self.text("note.md", "Synthetic")
        source = self.text("source.txt", "First")
        one = self.lib.add_entry(self.root, card, note, source)
        duplicate = self.lib.add_entry(self.root, card, note, source)
        self.assertEqual(one["id"], duplicate["id"])
        self.assertTrue(duplicate["duplicate"])
        source.write_text("Second", encoding="utf-8")
        two = self.lib.add_entry(self.root, card, note, source)
        self.assertNotEqual(one["id"], two["id"])
        self.assertIsNone(self.state()["entries"][0]["active_revision"])

    def test_revision_preserves_original_source(self):
        card = self.json_file("card.json", self.card())
        source = self.text("source.txt", "Original\r\nBytes")
        before = source.read_bytes()
        one = self.lib.add_entry(self.root, card, self.text("note.md", "First note"), source)
        old = self.root / "entries" / one["id"] / "1"
        old_bytes = {str(p.relative_to(old)): p.read_bytes() for p in old.rglob("*") if p.is_file()}
        two = self.lib.add_entry(self.root, card, self.text("note.md", "Changed note"), source, entry_id=one["id"])
        self.assertEqual(two["revision"], 2)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual({str(p.relative_to(old)): p.read_bytes() for p in old.rglob("*") if p.is_file()}, old_bytes)

    def test_source_changes_during_copy_is_not_registered(self):
        source = self.text("source.txt", "Before")
        before = (self.root / "library.json").read_bytes()
        original = self.lib.shutil.copyfileobj
        def changing(src, dst, *args):
            original(src, dst, *args)
            source.write_text("After", encoding="utf-8")
        with patch.object(self.lib.shutil, "copyfileobj", side_effect=changing):
            with self.assertRaises(self.lib.LibraryError) as caught:
                self.lib.add_entry(self.root, self.json_file("card.json", self.card()),
                                   self.text("note.md", "Note"), source)
        self.assertEqual((self.root / "library.json").read_bytes(), before)
        self.assertTrue(caught.exception.residual_paths)

    def test_library_cannot_overlap_project_or_skill(self):
        project = self.base / "song"
        self.p.init_project(project, "Song", self.text("brief.txt", "Brief"))
        for target in (project / "library", SCRIPTS / "library", SCRIPTS.parent):
            with self.assertRaises(self.lib.LibraryError):
                self.lib.init_library(target)

    def test_lock_and_persistent_denial_preserve_manifest(self):
        lock = self.root / ".skill-library.lock"
        lock.write_text("another writer")
        with self.assertRaises(self.lib.LibraryError):
            self.draft()
        self.assertEqual(lock.read_text(), "another writer")
        lock.unlink()
        before = (self.root / "library.json").read_bytes()
        with patch.object(self.lib.io.os, "replace", side_effect=PermissionError(13, "denied", None, 5)):
            with self.assertRaises(self.lib.LibraryError) as caught:
                self.draft()
        self.assertEqual((self.root / "library.json").read_bytes(), before)
        self.assertTrue(caught.exception.residual_paths)

    def test_size_secret_and_bool_inputs_reject_without_echo(self):
        for note in ("x" * (2 * 1024 * 1024 + 1), "api_key=synthetic-credential-value"):
            with self.assertRaises(self.lib.LibraryError) as caught:
                self.lib.add_entry(self.root, self.json_file("card.json", self.card()), self.text("note.md", note))
            self.assertNotIn("synthetic-credential-value", str(caught.exception))
        with self.assertRaises(self.lib.LibraryError):
            self.draft(permissions=dict(assistant_read="false"))
        self.assertEqual(self.state()["entries"], [])

    def test_missing_or_escaped_entry_is_not_an_empty_library(self):
        item = self.draft()
        path = self.root / "entries" / item["id"] / "1/note.md"
        path.unlink()
        with self.assertRaises(self.lib.LibraryError):
            self.lib.library_status(self.root)


class LibraryApprovalTest(LibraryCase):
    def test_persisted_card_requires_complete_schema(self):
        item = self.draft()
        self.approve(item)
        path = self.root / "entries" / item["id"] / "1/entry.json"
        original, index = json.loads(path.read_text(encoding="utf-8")), self.state()
        mutations = [dict(original, **{key: None}) for key in ("tags", "permissions")]
        mutations += [{k: v for k, v in original.items() if k != key} for key in ("tags", "permissions", "avoid_when")]
        mutations.append(dict(original, permissions=dict(assistant_read=True)))
        for data in mutations:
            with self.subTest(data=data):
                path.write_text(json.dumps(data), encoding="utf-8")
                state = json.loads(json.dumps(index))
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                state["entries"][0]["revisions"][0]["sha256"] = digest
                state["entries"][0]["events"][0]["sha256"] = digest
                (self.root / "library.json").write_text(json.dumps(state), encoding="utf-8")
                for action in (lambda: self.lib.library_status(self.root), lambda: self.query(),
                               lambda: self.lib.get_entry(self.root, item["id"], 1)):
                    with self.assertRaises(self.lib.LibraryError) as caught:
                        action()
                    self.assertEqual(caught.exception.code, "schema")

    def test_approval_and_revoke_control_search(self):
        item = self.draft()
        path = self.root / "entries" / item["id"] / "1/entry.json"
        before = path.read_bytes()
        self.assertEqual(self.query()["items"], [])
        self.approve(item)
        self.assertEqual(self.query()["items"][0]["revision"], 1)
        self.lib.revoke_entry(self.root, item["id"], self.text("reason.md", "Withdraw synthetic reference"))
        self.assertEqual(self.query()["items"], [])
        self.assertEqual(path.read_bytes(), before)

    def test_new_draft_keeps_old_approved_revision_active(self):
        one = self.draft()
        self.approve(one)
        two = self.lib.add_entry(self.root, self.json_file("card.json", self.card()),
                                 self.text("note.md", "Changed"), entry_id=one["id"])
        self.assertEqual(self.query()["items"][0]["revision"], 1)
        self.approve(two)
        self.assertEqual(self.query()["items"][0]["revision"], 2)
        self.assertEqual(self.lib.get_entry(self.root, one["id"], 1)["note"], "Use a short repeating chorus motif.")

    def test_stale_approval_hash_rejected(self):
        item = self.draft()
        with self.assertRaises(self.lib.LibraryError):
            self.lib.approve_entry(self.root, item["id"], 1,
                self.json_file("approval.json", dict(sha256="0" * 64, approval="Yes")))
        self.assertEqual(self.query()["items"], [])

    def test_scope_false_or_string_not_usable(self):
        for scope in ({}, dict(assistant_read=True, reuse=False)):
            item = self.draft(permissions=scope)
            self.approve(item)
        self.assertEqual(self.query()["items"], [])
        with self.assertRaises(self.lib.LibraryError):
            self.lib.get_entry(self.root, self.state()["entries"][0]["id"], 1)

    def test_chinese_fullwidth_query_and_empty_hits(self):
        item = self.draft(title="\u8f7b\u5feb\u526f\u6b4c ABC", tags=["\uff30\uff2f\uff30"])
        self.approve(item)
        result = self.query(keywords=["\u526f\u6b4c", "abc"], tags=["pop"])
        self.assertEqual(result["total"], 1)
        self.assertIn("title", result["items"][0]["matched_fields"])
        self.assertEqual(result["items"][0]["title"], "\u8f7b\u5feb\u526f\u6b4c ABC")
        self.assertEqual(self.query(keywords=["not-present"])["items"], [])
        self.assertEqual(self.query(offset=1, limit=1)["items"], [])
        for value in (-1, True):
            with self.assertRaises(self.lib.LibraryError):
                self.query(offset=value)

    def test_corrupted_attachment_reported_not_silently_omitted(self):
        item = self.lib.add_entry(self.root, self.json_file("card.json", self.card()),
                                  self.text("note.md", "Note"), self.text("source.txt", "Source"))
        self.approve(item)
        (self.root / "entries" / item["id"] / "1/source/source.txt").write_text("Changed")
        with self.assertRaises(self.lib.LibraryError):
            self.query()

    def test_current_approval_cannot_be_invented_in_index(self):
        item = self.draft()
        state = self.state()
        state["entries"][0]["active_revision"] = 1
        (self.root / "library.json").write_text(json.dumps(state))
        with self.assertRaises(self.lib.LibraryError):
            self.query()

    def test_cli_missing_library_is_read_only_and_errors_redacted(self):
        out = subprocess.run([sys.executable, "-I", "-S", str(SCRIPTS / "library.py"), "search"],
                             cwd=self.base, capture_output=True, text=True, timeout=15)
        self.assertEqual(out.returncode, 2)
        self.assertEqual(json.loads(out.stderr)["error"], "usage")


if __name__ == "__main__":
    unittest.main()
