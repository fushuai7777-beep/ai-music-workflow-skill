"""Explicit, local producer reference records. No model or network access."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import unicodedata
import uuid
from urllib.parse import urlsplit


def _sibling(name):
    key = "_ai_music_" + name
    path = Path(__file__).resolve().with_name(name + ".py")
    cached = sys.modules.get(key)
    if cached is not None and Path(cached.__file__).resolve() == path:
        return cached
    spec = importlib.util.spec_from_file_location(key, path)
    result = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), result.__dict__)
    sys.modules[key] = result
    return result


io = _sibling("record_io")
LibraryError = io.RecordError
KINDS = ("audio_reference", "lyrics_example", "prompt", "preference", "avoid", "lesson")
SCOPES = ("assistant_read", "reuse", "external_generation", "share")
SUFFIXES = (".txt", ".md", ".json", ".docx", ".pdf", ".wav", ".mp3", ".m4a", ".flac")
SKILL_ROOT = Path(__file__).resolve().parents[1]


def disjoint(left, right):
    left, right = io.root(left), io.root(right)
    io.require(not left.is_relative_to(right) and not right.is_relative_to(left),
               "path", "Library, project and Skill directories must be separate.")


def _root(path):
    path = io.root(path)
    disjoint(path, SKILL_ROOT)
    io.require(not any((p / "project.json").exists() for p in (path, *path.parents)),
               "path", "Library cannot be inside a song project.")
    return path


def _uuid(value):
    io.require(isinstance(value, str), "schema", "Invalid stable identity.")
    try:
        valid = str(uuid.UUID(value)) == value
    except ValueError:
        valid = False
    io.require(valid, "schema", "Invalid stable identity.")


def _hash(value):
    io.require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
               "schema", "Invalid content hash.")


def _safe_text(value, *, empty=False):
    value = io.clean(value, empty=empty)
    io.require(not re.search(r'(?i)\b(?:api[_ -]?key|app[_ -]?secret|access[_ -]?token|endpoint[_ -]?id|cookie|password)["\x27]?\s*[:=]', value),
               "secret", "Potential sensitive input detected; content not stored.")
    for match in re.finditer(r"https?://[^\s<>\"']+", value):
        try:
            url = urlsplit(match.group())
            safe = not url.username and not url.password and not url.query and not url.fragment
        except ValueError:
            safe = False
        io.require(safe, "source", "Use ordinary source links without credentials or query parameters.")
    return value


def _card(value):
    fields = {"kind", "title", "tags", "applies_to", "avoid_when", "source", "permissions"}
    io.require(isinstance(value, dict) and set(value) <= fields
               and {"kind", "title", "applies_to", "source"} <= set(value),
               "schema", "Invalid material card fields.")
    io.require(value["kind"] in KINDS, "schema", "Unknown material kind.")
    result = {key: _safe_text(value.get(key, ""), empty=key == "avoid_when")
              for key in ("title", "applies_to", "avoid_when")}
    tags = value.get("tags", [])
    io.require(isinstance(tags, list), "schema", "Tags must be a list.")
    result["tags"] = list(dict.fromkeys(_safe_text(tag) for tag in tags))
    scope = value.get("permissions", {})
    io.require(isinstance(scope, dict) and set(scope) <= set(SCOPES)
               and all(type(v) is bool for v in scope.values()), "scope", "Permissions must be explicit booleans.")
    result["permissions"] = {key: scope.get(key, False) for key in SCOPES}
    source = value["source"]
    io.require(isinstance(source, dict) and {"type", "description"} <= set(source)
               and all(isinstance(k, str) and isinstance(v, str) for k, v in source.items()),
               "source", "Source requires a type and factual description with text values.")
    result["source"] = {k: _safe_text(v) for k, v in source.items()}
    _safe_text(json.dumps(source, ensure_ascii=False))
    result["kind"] = value["kind"]
    return result


def _verify(root, relative, digest):
    _hash(digest)
    path = io.inside(root, relative)
    io.require(path.is_file() and io.digest(path) == digest,
               "integrity", "Library file missing or changed; restore or create a new version.")
    return path


def _entry(root, row, rev):
    path = _verify(root, rev["path"], rev["sha256"])
    data = io.read_json(path)
    card_fields = ("kind", "title", "tags", "applies_to", "avoid_when", "source", "permissions")
    io.require(set(data) == set(card_fields) | {"id", "revision", "note_path", "note_sha256", "attachment"},
               "schema", "Stored material must contain all versioned fields.")
    scope = data["permissions"]
    io.require(isinstance(scope, dict) and set(scope) == set(SCOPES)
               and all(type(value) is bool for value in scope.values()),
               "schema", "Stored permissions must contain all four booleans.")
    io.require(data.get("id") == row["id"] and type(data.get("revision")) is int
               and data["revision"] == rev["revision"], "integrity", "Entry identity mismatch.")
    _card({key: data[key] for key in card_fields})
    prefix = f"entries/{row['id']}/{rev['revision']}/"
    io.require(data.get("note_path") == prefix + "note.md", "path", "Invalid note path.")
    _verify(root, data["note_path"], data.get("note_sha256"))
    io.require("attachment" in data, "schema", "Missing attachment field.")
    attachment = data["attachment"]
    if attachment is not None:
        io.require(isinstance(attachment, dict) and set(attachment) == {"path", "sha256", "bytes"},
                   "schema", "Invalid attachment fields.")
        io.require(isinstance(attachment["path"], str) and attachment["path"].startswith(prefix + "source/")
                   and len(Path(attachment["path"]).parts) == 5,
                   "path", "Invalid attachment path.")
        file = _verify(root, attachment["path"], attachment["sha256"])
        io.require(type(attachment["bytes"]) is int and file.stat().st_size == attachment["bytes"],
                   "integrity", "Attachment size mismatch.")
    return data


def _find(state, identity):
    rows = [row for row in state["entries"] if row["id"] == identity]
    io.require(len(rows) == 1, "identity", "Material not found or ambiguous.")
    return rows[0]


def _revision(row, revision):
    io.require(type(revision) is int and revision > 0, "revision", "Expected a positive revision.")
    rows = [item for item in row["revisions"] if item["revision"] == revision]
    io.require(len(rows) == 1, "revision", "Revision not found.")
    return rows[0]


def _load(root):
    state = io.read_json(io.inside(root, "library.json"))
    io.require(type(state.get("schema_version")) is int and state["schema_version"] == 1,
               "schema", "Unsupported library schema; no automatic migration.")
    _uuid(state.get("library_id"))
    io.require(all(isinstance(state.get(k), str) and state[k] for k in ("created_at", "updated_at"))
               and isinstance(state.get("entries"), list), "schema", "Invalid library record.")
    seen = set()
    for row in state["entries"]:
        io.require(isinstance(row, dict) and {"id", "revisions", "active_revision", "events"} <= set(row),
                   "schema", "Invalid entry index.")
        _uuid(row["id"])
        io.require(row["id"] not in seen, "schema", "Duplicate library identity.")
        seen.add(row["id"])
        io.require(isinstance(row["revisions"], list) and bool(row["revisions"])
                   and isinstance(row["events"], list), "schema", "Invalid revision index.")
        revisions = set()
        for rev in row["revisions"]:
            io.require(isinstance(rev, dict) and set(rev) == {"revision", "path", "sha256"}
                       and type(rev["revision"]) is int and rev["revision"] > 0,
                       "schema", "Invalid revision record.")
            io.require(rev["revision"] not in revisions
                       and rev["path"] == f"entries/{row['id']}/{rev['revision']}/entry.json",
                       "schema", "Duplicate revision or invalid path.")
            revisions.add(rev["revision"])
            _entry(root, row, rev)
        active = None
        for event in row["events"]:
            io.require(isinstance(event, dict) and event.get("action") in ("approve", "revoke")
                       and isinstance(event.get("at"), str) and bool(event["at"]), "schema", "Invalid approval event.")
            _safe_text(event.get("text"))
            if event["action"] == "approve":
                rev = _revision(row, event.get("revision"))
                io.require(event.get("sha256") == rev["sha256"], "integrity", "Approval content mismatch.")
                active = rev["revision"]
            else:
                active = None
        io.require(row["active_revision"] == active and
                   (active is None or type(row["active_revision"]) is int), "integrity", "Active revision lacks approval.")
    return state


@contextmanager
def _locked(root):
    lock = io.inside(root, ".skill-library.lock")
    token = io.new_id()
    try:
        with lock.open("x", encoding="ascii") as handle:
            handle.write(token)
    except FileExistsError as exc:
        raise LibraryError("locked", "Library locked; verify the writer before recovery.") from exc
    except OSError as exc:
        raise LibraryError("io", "Cannot access the library lock.") from exc
    try:
        yield
    except OSError as exc:
        raise LibraryError("io", "Library operation failed; completion not recorded.") from exc
    finally:
        if lock.exists() and lock.read_text(encoding="ascii") == token:
            lock.unlink()


def _save(root, state):
    state["updated_at"] = io.now()
    io.atomic_json(root, "library.json", state)


def init_library(root):
    root = _root(root)
    io.require(not root.exists() or (root.is_dir() and not any(root.iterdir())),
               "exists", "Library directory must be new or empty; use status for an existing library.")
    root.mkdir(parents=True, exist_ok=True)
    with _locked(root):
        io.require(not any(p.name != ".skill-library.lock" for p in root.iterdir()), "exists", "Library is not empty.")
        state = dict(schema_version=1, library_id=io.new_id(), created_at=io.now(), updated_at=io.now(), entries=[])
        _save(root, state)
    return state


def library_status(root):
    root = _root(root)
    with _locked(root):
        state = _load(root)
        return dict(library_id=state["library_id"], entries=[dict(id=r["id"], active_revision=r["active_revision"],
                    revisions=len(r["revisions"])) for r in state["entries"]])


def _source(path):
    path = Path(path).resolve()
    io.require(path.is_file() and path.suffix.lower() in SUFFIXES,
               "source", "Expected an explicitly selected supported source file.")
    io.require(path.name.lower() not in ("config.json", "credentials.json", "cookies.json", "secrets.json")
               and not any(x in path.name.lower() for x in ("token", "credential", "cookie", "secret")),
               "secret", "Credential files are not material sources.")
    if path.suffix.lower() in (".txt", ".md", ".json"):
        _safe_text(io.read_text(path))
    return path


def add_entry(root, card_file, note_file, source_file=None, *, entry_id=None):
    return _add(root, _card(io.read_json(card_file)), _safe_text(io.read_text(note_file)), source_file, entry_id)


def _add(root, card, note, source_file=None, entry_id=None):
    root = _root(root)
    if entry_id is not None:
        _uuid(entry_id)
    source = _source(source_file) if source_file is not None else None
    residual = None
    try:
        with _locked(root):
            state = _load(root)
            source_stat, source_hash = (source.stat(), io.digest(source)) if source else (None, None)
            if source and entry_id is None:
                for row in state["entries"]:
                    for rev in row["revisions"]:
                        entry = _entry(root, row, rev)
                        if entry["attachment"] and entry["attachment"]["sha256"] == source_hash:
                            return dict(id=row["id"], revision=rev["revision"], sha256=rev["sha256"],
                                        status="active" if row["active_revision"] == rev["revision"] else "inactive", duplicate=True)
            row = _find(state, entry_id) if entry_id is not None else dict(id=io.new_id(), revisions=[], active_revision=None, events=[])
            revision = max((r["revision"] for r in row["revisions"]), default=0) + 1
            prefix = f"entries/{row['id']}/{revision}/"
            residual = prefix.rstrip("/")
            folder = io.inside(root, residual)
            io.require(not folder.exists(), "exists", "Unregistered revision exists; inspect residual files first.")
            note_path = io.write_new(root, prefix + "note.md", note)
            attachment = None
            if source:
                relative = prefix + "source/" + source.name
                target = io.inside(root, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open("rb") as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                after = source.stat()
                io.require((source_stat.st_size, source_stat.st_mtime_ns, source_stat.st_ino) ==
                           (after.st_size, after.st_mtime_ns, after.st_ino)
                           and io.digest(source) == source_hash and io.digest(target) == source_hash,
                           "integrity", "Source changed during copy; revision not registered.")
                attachment = dict(path=relative, sha256=source_hash, bytes=after.st_size)
            data = dict(card, id=row["id"], revision=revision, note_path=prefix + "note.md",
                        note_sha256=io.digest(note_path), attachment=attachment)
            path = io.write_new(root, prefix + "entry.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            rev = dict(revision=revision, path=prefix + "entry.json", sha256=io.digest(path))
            row["revisions"].append(rev)
            if entry_id is None:
                state["entries"].append(row)
            _save(root, state)
        return dict(id=row["id"], revision=revision, sha256=rev["sha256"], status="draft", duplicate=False)
    except LibraryError as exc:
        if residual and io.inside(root, residual).exists():
            exc.residual_paths.append(residual)
        raise


def approve_entry(root, entry_id, revision, approval_file):
    root, approval = _root(root), io.read_json(approval_file)
    io.require(set(approval) == {"sha256", "approval"}, "approval", "Expected content hash and approval text.")
    text = _safe_text(approval["approval"])
    with _locked(root):
        state = _load(root)
        row = _find(state, entry_id)
        rev = _revision(row, revision)
        io.require(approval["sha256"] == rev["sha256"], "approval", "Approval must match the exact content hash.")
        row["events"].append(dict(action="approve", revision=revision, sha256=rev["sha256"], text=text, at=io.now()))
        row["active_revision"] = revision
        _save(root, state)
    return dict(id=entry_id, revision=revision, status="active", sha256=rev["sha256"])


def revoke_entry(root, entry_id, reason_file):
    root, reason = _root(root), _safe_text(io.read_text(reason_file))
    with _locked(root):
        state = _load(root)
        row = _find(state, entry_id)
        row["events"].append(dict(action="revoke", text=reason, at=io.now()))
        row["active_revision"] = None
        _save(root, state)
    return dict(id=entry_id, status="revoked")


def _view(root, row, rev):
    data = _entry(root, row, rev)
    io.require(data["permissions"]["assistant_read"], "scope", "Assistant read permission is absent.")
    return dict(data, sha256=rev["sha256"], note=_safe_text(io.read_text(io.inside(root, data["note_path"]))),
                status="active" if row["active_revision"] == rev["revision"] else "inactive")


def get_entry(root, entry_id, revision=None):
    root = _root(root)
    with _locked(root):
        row = _find(_load(root), entry_id)
        rev = _revision(row, row["active_revision"] if revision is None else revision)
        return _view(root, row, rev)


def _normalized(value):
    return unicodedata.normalize("NFKC", value).casefold()


def search_library(root, query_file):
    root, query = _root(root), io.read_json(query_file)
    io.require(set(query) <= {"keywords", "tags", "kinds", "offset", "limit"}, "query", "Unknown query fields.")
    filters = {}
    for key in ("keywords", "tags", "kinds"):
        values = query.get(key, [])
        io.require(isinstance(values, list), "query", "Query filters must be lists.")
        filters[key] = [_normalized(_safe_text(v)) for v in values]
    io.require(all(k in KINDS for k in filters["kinds"]), "query", "Unknown kind filter.")
    offset, limit = query.get("offset", 0), query.get("limit", 20)
    io.require(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 100,
               "query", "Expected nonnegative offset and limit between 1 and 100.")
    results = []
    with _locked(root):
        state = _load(root)
        for row in state["entries"]:
            if row["active_revision"] is None:
                continue
            rev = _revision(row, row["active_revision"])
            data = _entry(root, row, rev)
            if not data["permissions"]["assistant_read"] or not data["permissions"]["reuse"]:
                continue
            if filters["kinds"] and data["kind"] not in filters["kinds"]:
                continue
            if not set(filters["tags"]) <= {_normalized(t) for t in data["tags"]}:
                continue
            data = _view(root, row, rev)
            fields = {k: _normalized(" ".join(data[k]) if k == "tags" else data[k])
                      for k in ("title", "tags", "applies_to", "avoid_when", "note")}
            if not all(any(word in value for value in fields.values()) for word in filters["keywords"]):
                continue
            matched = [k for k, value in fields.items() if any(w in value for w in filters["keywords"])]
            if filters["tags"] and "tags" not in matched:
                matched.append("tags")
            score = sum(w in fields[k] for w in filters["keywords"] for k in ("title", "tags"))
            results.append((score, dict(data, matched_fields=matched)))
        results.sort(key=lambda item: (-item[0], item[1]["id"]))
        return dict(library_id=state["library_id"], items=[r[1] for r in results[offset:offset + limit]],
                    total=len(results), offset=offset, limit=limit, has_more=offset + limit < len(results))


def draft_lesson(root, project_root, archive_id, card_file, note_file):
    root, project_root = _root(root), io.root(project_root)
    disjoint(root, project_root)
    card = _card(io.read_json(card_file))
    io.require(card["kind"] == "lesson", "kind", "Project experience must be a lesson draft.")
    note = _safe_text(io.read_text(note_file))
    project = _sibling("project")
    with project._project_lock(project_root):
        state = project._load(project_root)
        archive = project._find(state["archives"], archive_id)
        folder = io.inside(project_root, archive["path"])
        manifest = _verify(folder, "manifest.json", archive["manifest_sha256"])
        data = io.read_json(manifest)
        io.require(data.get("project_id") == state["project_id"] and data.get("archive_id") == archive_id
                   and data.get("selected_ids") == archive["selected_ids"], "integrity", "Archive identity mismatch.")
        io.require(isinstance(data.get("files"), list) and bool(data["files"]), "schema", "Archive file list missing.")
        seen = set()
        for row in data["files"]:
            io.require(isinstance(row, dict) and set(row) == {"path", "sha256"}
                       and isinstance(row["path"], str) and row["path"] not in seen,
                       "schema", "Invalid archive file list.")
            seen.add(row["path"])
            _verify(folder, row["path"], row["sha256"])
        io.require("review.md" in seen, "integrity", "Archive lacks a checksummed review.")
        card["source"] = dict(type="project_archive", description=card["source"]["description"],
                              project_id=state["project_id"], archive_id=archive_id,
                              manifest_sha256=archive["manifest_sha256"])
    return _add(root, card, note)


def selection_shape(selection):
    io.require(isinstance(selection, dict) and set(selection) == {"library_id", "approval", "items"},
               "selection", "Expected library identity, approval and selected items.")
    _uuid(selection["library_id"])
    _safe_text(selection["approval"])
    io.require(isinstance(selection["items"], list) and bool(selection["items"]), "selection", "Select at least one material.")
    seen = set()
    for item in selection["items"]:
        io.require(isinstance(item, dict) and set(item) == {"entry_id", "revision", "sha256", "summary", "usage"},
                   "selection", "Invalid selected material fields.")
        _uuid(item["entry_id"])
        _hash(item["sha256"])
        io.require(type(item["revision"]) is int and item["revision"] > 0 and item["entry_id"] not in seen,
                   "selection", "Invalid or duplicate selected revision.")
        seen.add(item["entry_id"])
        _safe_text(item["summary"])
        _safe_text(item["usage"])


@contextmanager
def validated_selection(root, selection, purpose):
    root = _root(root)
    selection_shape(selection)
    io.require(purpose in ("local", "generation"), "scope", "Unknown use purpose.")
    with _locked(root):
        state = _load(root)
        io.require(state["library_id"] == selection["library_id"], "identity", "This is a different library.")
        permissions = []
        for item in selection["items"]:
            row = _find(state, item["entry_id"])
            rev = _revision(row, item["revision"])
            io.require(row["active_revision"] == item["revision"] and rev["sha256"] == item["sha256"],
                       "approval", "Selected revision is not currently approved.")
            data = _entry(root, row, rev)
            scope = data["permissions"]
            io.require(scope["assistant_read"] and scope["reuse"]
                       and (purpose == "local" or scope["external_generation"]),
                       "scope", "Selected material is not authorized for this purpose.")
            permissions.append(dict(entry_id=item["entry_id"], **scope))
        yield dict(selection=selection, permissions=permissions)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LibraryError("usage", "Invalid arguments; use --help.")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = _Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    definitions = {"init": [], "status": [], "add": [("card-file", Path), ("note-file", Path)],
                   "approve": [("entry-id", str), ("revision", int), ("approval-file", Path)],
                   "revoke": [("entry-id", str), ("reason-file", Path)],
                   "search": [("query-file", Path)], "show": [("entry-id", str)],
                   "draft-lesson": [("project", Path), ("archive-id", str), ("card-file", Path), ("note-file", Path)]}
    for name, arguments in definitions.items():
        command = sub.add_parser(name)
        command.add_argument("--library", type=Path, required=True)
        for flag, kind in arguments:
            command.add_argument("--" + flag, type=kind, required=True)
        if name == "add":
            command.add_argument("--source-file", type=Path)
            command.add_argument("--entry-id")
        if name == "show":
            command.add_argument("--revision", type=int)
    try:
        args = parser.parse_args(argv)
        handlers = {"init": lambda: init_library(args.library), "status": lambda: library_status(args.library),
                    "add": lambda: add_entry(args.library, args.card_file, args.note_file, args.source_file, entry_id=args.entry_id),
                    "approve": lambda: approve_entry(args.library, args.entry_id, args.revision, args.approval_file),
                    "revoke": lambda: revoke_entry(args.library, args.entry_id, args.reason_file),
                    "search": lambda: search_library(args.library, args.query_file),
                    "show": lambda: get_entry(args.library, args.entry_id, args.revision),
                    "draft-lesson": lambda: draft_lesson(args.library, args.project, args.archive_id, args.card_file, args.note_file)}
        print(json.dumps(handlers[args.command](), ensure_ascii=False))
        return 0
    except LibraryError as exc:
        result = dict(error=exc.code, message=str(exc))
        if exc.residual_paths:
            result["residual_paths"] = exc.residual_paths
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print(json.dumps(dict(error="io", message="Cannot access library files.")), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
