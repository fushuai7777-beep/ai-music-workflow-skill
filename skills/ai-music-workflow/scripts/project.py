"""Local project records. No network, model, or browser dependencies."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any
from urllib.parse import urlsplit
import wave


SKILL_ROOT = Path(__file__).resolve().parents[1]
def _sibling(name):
    import importlib.util
    module_name = "_ai_music_" + name
    path = Path(__file__).resolve().with_name(name + ".py")
    cached = sys.modules.get(module_name)
    if cached is not None and Path(cached.__file__).resolve() == path:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, path)
    result = importlib.util.module_from_spec(spec)
    # Fixed sibling code only; never write runtime caches into the installed Skill.
    exec(compile(path.read_bytes(), str(path), "exec"), result.__dict__)
    sys.modules[module_name] = result
    return result


_io = _sibling("record_io")
ProjectError = _io.RecordError
MAX_TEXT_BYTES = _io.MAX_TEXT_BYTES
SECRET = _io.SECRET
_require, _now, _id = _io.require, _io.now, _io.new_id
_clean, _text, _json = _io.clean, _io.read_text, _io.read_json
_path, _digest = _io.inside, _io.digest
_atomic_move, _write_new = _io.atomic_move, _io.write_new


def _root(root: Path) -> Path:
    root = _io.root(root)
    _require(not root.is_relative_to(SKILL_ROOT), "path", "Project must be outside the Skill directory.")
    _require(not any((parent / "library.json").exists() for parent in (root, *root.parents)),
             "path", "Project must be outside the producer library.")
    return root


def _load(root: Path) -> dict[str, Any]:
    data = _json(_path(root, "project.json"))
    _require(type(data.get("schema_version")) is int and data["schema_version"] == 1,
             "schema", "Unsupported project schema.")
    for name in ("lyrics", "batches", "candidates", "reviews", "final_selection", "archives"):
        _require(isinstance(data.get(name), list), "schema", "Invalid project collection.")
    _fields(data, dict(project_id=str, title=str, created_at=str, updated_at=str))
    _path(root, data.get("brief_path"))
    fields = {
        "lyrics": dict(path=str, sha256=str, approval=(dict, type(None))),
        "batches": dict(lyrics_id=(str, type(None)), package_path=str, package_sha256=str,
                        authorization=dict, submission_status=str, song_ids=list, evidence=list),
        "candidates": dict(batch_id=str, song_id=str, source_page=str, path=str, sha256=str, media_check=dict),
        "archives": dict(path=str, manifest_sha256=str, selected_ids=list, created_at=str),
    }
    for collection, required in fields.items():
        identities = set()
        for row in data[collection]:
            _fields(row, dict(id=str, **required))
            _require(row["id"] not in identities, "schema", "Duplicate project identity.")
            identities.add(row["id"])
            _path(root, row["package_path"] if collection == "batches" else row["path"])
    for lyric in data["lyrics"]:
        if lyric["approval"] is not None:
            _fields(lyric["approval"], dict(text=str, sha256=str, at=str))
    for batch in data["batches"]:
        _require(batch["submission_status"] in ("prepared", "uncertain", "submitted", "not_submitted"),
                 "schema", "Unknown submission state; generation is blocked.")
        _identities(batch["song_ids"])
        _require(bool(batch["song_ids"]) == (batch["submission_status"] == "submitted"),
                 "schema", "Song identities do not match submission state.")
        _fields(batch["authorization"], dict(approval=str, max_generation_clicks=int))
        _require(type(batch["authorization"]["max_generation_clicks"]) is int
                 and batch["authorization"]["max_generation_clicks"] == 1,
                 "schema", "Invalid stored generation scope.")
        for evidence in batch["evidence"]:
            _fields(evidence, dict(at=str, status=str, text=str))
    _identities(data["final_selection"])
    _formats(data.get("required_formats", []))
    for row in data["reviews"]:
        _fields(row, dict(at=str, notes=str, rejected_ids=list))
        _identities(row["rejected_ids"])
        if "selected_ids" in row:
            _identities(row["selected_ids"])
    for row in data["archives"]:
        _identities(row["selected_ids"])
    references = data.get("references", [])
    _require(isinstance(references, list), "schema", "Invalid reference collection.")
    seen = set()
    for row in references:
        _fields(row, dict(id=str, path=str, sha256=str))
        _identities([row["id"]])
        _require(row["id"] not in seen and row["path"] == f"references/{row['id']}.json",
                 "schema", "Duplicate reference identity or invalid path.")
        seen.add(row["id"])
        _path(root, row["path"])
    for ref_id in [data.get("active_reference_id")] + [b.get("reference_id") for b in data["batches"]]:
        _require(ref_id is None or (isinstance(ref_id, str) and ref_id in seen),
                 "schema", "Reference identity is not tracked by this project.")
    events = data.get("reference_events", [])
    _require(isinstance(events, list), "schema", "Invalid reference event collection.")
    for event in events:
        _fields(event, dict(at=str, action=str, reason=str))
        _require(set(event) == {"at", "action", "reason", "reference_id"} and event["action"] == "clear",
                 "schema", "Invalid reference event fields.")
        ref_id = event["reference_id"]
        _require(ref_id is None or (isinstance(ref_id, str) and ref_id in seen),
                 "schema", "Unknown reference in project history.")
    return data


def _fields(row: Any, fields: dict) -> None:
    _require(isinstance(row, dict) and all(key in row and isinstance(row[key], kind)
             and (not isinstance(row[key], str) or bool(row[key].strip())) for key, kind in fields.items()),
             "schema", "Missing or invalid project record fields.")


def _identities(values: Any) -> None:
    _require(isinstance(values, list) and all(isinstance(value, str)
             and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) for value in values),
             "schema", "Invalid project identity list.")


def _formats(values: Any) -> list[str]:
    _require(isinstance(values, list) and all(value in ("mp3", "wav") for value in values),
             "formats", "Required formats must be a list containing mp3 and/or wav.")
    return sorted(set(values))


def _missing_formats(state: dict[str, Any]) -> dict[str, list[str]]:
    selected = [_find(state["candidates"], identity) for identity in state["final_selection"]]
    missing = {}
    for song_id in {row["song_id"] for row in selected}:
        present = {Path(row["path"]).suffix.lower()[1:] for row in selected if row["song_id"] == song_id}
        needed = sorted(set(state.get("required_formats", [])) - present)
        if needed:
            missing[song_id] = needed
    return missing


def _save(root: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    _io.atomic_json(root, "project.json", data)


@contextmanager
def _project_lock(root: Path):
    lock = _path(root, ".skill-project.lock")
    token = _id()
    try:
        with lock.open("x", encoding="ascii") as handle:
            handle.write(token)
    except FileExistsError as exc:
        raise ProjectError("locked", "Project is locked; verify the existing writer before recovery.") from exc
    try:
        yield
    except OSError as exc:
        raise ProjectError("io", "File operation failed; project completion was not recorded.") from exc
    finally:
        if lock.exists() and lock.read_text(encoding="ascii") == token:
            lock.unlink()


@contextmanager
def _edit(root: Path, initial: dict[str, Any] | None = None):
    with _project_lock(root):
        data = _load(root) if initial is None else initial
        yield data
        _save(root, data)





def _find(rows: list[dict[str, Any]], identity: str) -> dict[str, Any]:
    matches = [row for row in rows if row["id"] == identity]
    _require(len(matches) == 1, "identity", "Record not found or ambiguous.")
    return matches[0]


def _verify(root: Path, row: dict[str, Any], field: str = "path", hash_field: str = "sha256") -> Path:
    path = _path(root, row.get(field))
    _require(path.is_file() and _digest(path) == row.get(hash_field),
             "integrity", "Referenced file is missing or changed; restore or create a new version.")
    return path


def init_project(root: Path, title: str, brief_file: Path) -> dict[str, Any]:
    root = _root(root)
    title, brief = _clean(title), _text(brief_file)
    _require(not root.exists() or (root.is_dir() and not any(root.iterdir())),
             "exists", "Project directory must be new or empty.")
    root.mkdir(parents=True, exist_ok=True)
    state = dict(schema_version=1, project_id=_id(), title=title, created_at=_now(),
                 updated_at=_now(), brief_path="brief.md", lyrics=[], batches=[],
                 candidates=[], reviews=[], final_selection=[], archives=[])
    with _edit(root, state):
        _write_new(root, "brief.md", brief)
    return state


def add_lyrics(root: Path, source_file: Path) -> dict[str, Any]:
    root, content = _root(root), _text(source_file)
    with _edit(root) as state:
        identity = _id()
        relative = f"lyrics/{identity}.txt"
        path = _write_new(root, relative, content)
        row = dict(id=identity, path=relative, sha256=_digest(path), approval=None)
        state["lyrics"].append(row)
    return row


def approve_lyrics(root: Path, lyrics_id: str, approval_file: Path) -> dict[str, Any]:
    root, approval = _root(root), _text(approval_file)
    with _edit(root) as state:
        row = _find(state["lyrics"], lyrics_id)
        _verify(root, row)
        row["approval"] = dict(text=approval, sha256=row["sha256"], at=_now())
    return row


def prepare_batch(root: Path, package_file: Path, authorization_file: Path, *, library_root=None) -> dict[str, Any]:
    return _create_batch(root, package_file, authorization_file, library_root=library_root)


def import_existing_batch(root: Path, package_file: Path, authorization_file: Path,
                          observation_file: Path) -> dict[str, Any]:
    observation = _observation(observation_file)
    _require(observation["status"] == "submitted", "observation", "Import requires an already observed result.")
    return _create_batch(root, package_file, authorization_file, observation)


def _create_batch(root: Path, package_file: Path, authorization_file: Path,
                  observation: dict[str, Any] | None = None, *, library_root=None) -> dict[str, Any]:
    root, package, authorization = _root(root), _json(package_file), _json(authorization_file)
    _require(set(package) == {"title", "style", "lyrics_id", "lyrics", "instrumental"},
             "package", "Unexpected or missing package fields.")
    package["title"], package["style"] = _clean(package["title"]), _clean(package["style"])
    package["lyrics"] = _clean(package["lyrics"], empty=True)
    _require(type(package["instrumental"]) is bool, "package", "Instrumental mode must be a boolean.")
    _require(set(authorization) == {"approval", "max_generation_clicks"},
             "authorization", "Expected approval and max_generation_clicks.")
    authorization["approval"] = _clean(authorization["approval"])
    _require(type(authorization["max_generation_clicks"]) is int
             and authorization["max_generation_clicks"] == 1,
             "authorization", "One explicitly approved generation click per batch.")
    guard = _reference_guard(root, library_root) if observation is None else nullcontext(None)
    with guard as reference_id, _edit(root) as state:
        if observation is None:
            _require(state.get("active_reference_id") == reference_id, "changed", "References changed; recheck before preparing.")
            _require(not any(b["submission_status"] in ("prepared", "uncertain") for b in state["batches"]),
                     "pending", "Resolve the existing prepared or uncertain batch before creating another.")
        if package["instrumental"]:
            _require(package["lyrics_id"] is None and package["lyrics"] == "",
                     "package", "Instrumental packages must have no lyrics or lyric reference.")
        else:
            lyric = _find(state["lyrics"], package["lyrics_id"])
            path = _verify(root, lyric)
            _require(isinstance(lyric["approval"], dict)
                     and lyric["approval"].get("sha256") == lyric["sha256"],
                     "approval", "Lyrics are not approved.")
            _require(package["lyrics"] == _text(path), "package", "Lyrics differ from the approved version.")
        if observation is not None:
            overlaps = [b for b in state["batches"] if set(b["song_ids"]) & set(observation["song_ids"])]
            if overlaps:
                _require(len(overlaps) == 1, "identity", "Observed songs span existing batches.")
                existing = overlaps[0]
                saved = _verify(root, existing, "package_path", "package_sha256")
                _require(existing["song_ids"] == observation["song_ids"] and _json(saved) == package
                         and existing["authorization"] == authorization,
                         "identity", "Observed result conflicts with an existing batch.")
                _append_evidence(existing, "submitted", observation["evidence"])
                return existing
        identity = _id()
        relative = f"batches/{identity}/package.json"
        path = _write_new(root, relative, json.dumps(package, ensure_ascii=False, indent=2) + "\n")
        row = dict(id=identity, lyrics_id=package["lyrics_id"], package_path=relative,
                   package_sha256=_digest(path), authorization=authorization,
                   submission_status="prepared", song_ids=[], evidence=[])
        if reference_id is not None:
            row["reference_id"] = reference_id
        if observation is not None:
            row.update(origin="existing_result", submission_status="submitted", song_ids=observation["song_ids"])
            _append_evidence(row, "submitted", observation["evidence"])
        state["batches"].append(row)
    return row


def _observation(observation_file: Path) -> dict[str, Any]:
    observation = _json(observation_file)
    _require(set(observation) == {"status", "song_ids", "evidence"},
             "observation", "Expected status, song_ids and evidence.")
    status = observation["status"]
    _require(status in ("uncertain", "submitted", "not_submitted"), "state", "Invalid submission status.")
    evidence = _clean(observation["evidence"])
    ids = observation["song_ids"]
    _require(isinstance(ids, list) and all(isinstance(s, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", s)
                                        for s in ids), "identity", "Invalid observed song identities.")
    ids = sorted(set(ids))
    _require(bool(ids) if status == "submitted" else not ids,
             "observation", "Only submitted observations must include song identities.")
    return dict(status=status, song_ids=ids, evidence=evidence)


def _append_evidence(row: dict[str, Any], status: str, evidence: str) -> None:
    if not any(item["status"] == status and item["text"] == evidence for item in row["evidence"]):
        row["evidence"].append(dict(at=_now(), status=status, text=evidence))


def record_submission(root: Path, batch_id: str, observation_file: Path, *, library_root=None) -> dict[str, Any]:
    root, observation = _root(root), _observation(observation_file)
    status, ids, evidence = observation["status"], observation["song_ids"], observation["evidence"]
    prior = _find(_load(root)["batches"], batch_id)
    starting = status == "uncertain" and prior["submission_status"] == "prepared"
    guard = _reference_guard(root, library_root, batch_id) if starting else nullcontext(None)
    with guard as reference_id, _edit(root) as state:
        row = _find(state["batches"], batch_id)
        old = row["submission_status"]
        if old == status and row["song_ids"] == ids:
            _append_evidence(row, status, evidence)
            return row
        allowed = {"prepared": {"uncertain", "not_submitted"},
                   "uncertain": {"submitted", "not_submitted"}, "submitted": set(), "not_submitted": set()}
        _require(status in allowed.get(old, set()), "state", "Submission cannot transition or be reopened.")
        if status == "uncertain":
            _verify(root, row, "package_path", "package_sha256")
            _require(row.get("reference_id") == reference_id, "changed", "Batch references changed; recheck before submitting.")
            _generation_ready(root, state, row)
        row["submission_status"], row["song_ids"] = status, ids
        _append_evidence(row, status, evidence)
    return row


def _snapshot(root, state, reference_id):
    row = _find(state.get("references", []), reference_id)
    data = _json(_verify(root, row))
    _fields(data, dict(id=str, created_at=str, selection=dict, permissions=list))
    _require(data["id"] == reference_id, "integrity", "Reference snapshot identity mismatch.")
    _sibling("library").selection_shape(data["selection"])
    return data


def attach_references(root, library_root, selection_file):
    root, selection = _root(root), _json(selection_file)
    library = _sibling("library")
    library.disjoint(root, library_root)
    relative = None
    try:
        with library.validated_selection(library_root, selection, "local") as verified, _edit(root) as state:
            identity = _id()
            relative = f"references/{identity}.json"
            data = dict(verified, id=identity, created_at=_now())
            path = _write_new(root, relative, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            row = dict(id=identity, path=relative, sha256=_digest(path))
            state.setdefault("references", []).append(row)
            state["active_reference_id"] = identity
    except (ProjectError, OSError) as exc:
        error = exc if isinstance(exc, ProjectError) else ProjectError("io", "Reference save failed; completion not recorded.")
        if relative is not None:
            try:
                if _path(root, relative).exists():
                    error.residual_paths.append(relative)
            except (ProjectError, OSError):
                pass
        if error is exc:
            raise
        raise error from exc
    return row


def clear_references(root, reason_file):
    root, reason = _root(root), _text(reason_file)
    with _edit(root) as state:
        previous = state.get("active_reference_id")
        state["active_reference_id"] = None
        state.setdefault("reference_events", []).append(dict(at=_now(), action="clear", reference_id=previous, reason=reason))
    return dict(active_reference_id=None, previous_reference_id=previous)


@contextmanager
def _reference_guard(root, library_root, batch_id=None):
    state = _load(root)
    ref_id = (state.get("active_reference_id") if batch_id is None
              else _find(state["batches"], batch_id).get("reference_id"))
    if ref_id is None:
        yield None
        return
    snapshot = _snapshot(root, state, ref_id)
    _require(library_root is not None, "library", "Current library path is required for referenced generation.")
    library = _sibling("library")
    library.disjoint(root, library_root)
    # Keep the library stable until the project record is checked and saved.
    with library.validated_selection(library_root, snapshot["selection"], "generation"):
        yield ref_id


def _generation_ready(root, state, batch):
    _require(batch["submission_status"] == "prepared", "pending", "Only a prepared batch may start generation.")
    _require(not any(b["id"] != batch["id"] and b["submission_status"] in ("prepared", "uncertain")
                     for b in state["batches"]), "pending", "Resolve other pending batches first.")
    _clean(batch["authorization"]["approval"])
    payload = _json(_verify(root, batch, "package_path", "package_sha256"))
    _require(set(payload) == {"title", "style", "lyrics_id", "lyrics", "instrumental"}
             and payload["lyrics_id"] == batch["lyrics_id"]
             and payload["instrumental"] is (batch["lyrics_id"] is None), "integrity", "Batch package mode changed.")
    if batch["lyrics_id"] is not None:
        lyric = _find(state["lyrics"], batch["lyrics_id"])
        path = _verify(root, lyric)
        _require(isinstance(lyric["approval"], dict) and lyric["approval"].get("sha256") == lyric["sha256"]
                 and payload["lyrics"] == _text(path), "approval", "Batch lyrics are no longer verifiably approved.")
    else:
        _require(payload["lyrics"] == "", "integrity", "Instrumental package contains lyrics.")
    if batch.get("reference_id"):
        _snapshot(root, state, batch["reference_id"])


def check_generation(root, batch_id, library_root=None):
    root = _root(root)
    with _reference_guard(root, library_root, batch_id) as ref_id, _project_lock(root):
        state = _load(root)
        batch = _find(state["batches"], batch_id)
        _require(batch.get("reference_id") == ref_id, "changed", "Batch reference changed during validation.")
        _generation_ready(root, state, batch)
    return dict(ready=True, batch_id=batch_id, reference_id=ref_id, scope="local_preflight_only")


def _mp3_frame_size(header: bytes) -> int:
    if len(header) != 4:
        return 0
    bits = int.from_bytes(header, "big")
    version, layer = (bits >> 19) & 3, (bits >> 17) & 3
    bitrate, rate = (bits >> 12) & 15, (bits >> 10) & 3
    if bits >> 21 != 2047 or version == 1 or layer != 1 or bitrate in (0, 15) or rate == 3:
        return 0
    rates = (44100, 48000, 32000)
    br = ((0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
          if version == 3 else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144))
    sample_rate = rates[rate] // (1 if version == 3 else 2 if version == 2 else 4)
    return (144 if version == 3 else 72) * br[bitrate] * 1000 // sample_rate + ((bits >> 9) & 1)


def _media_check(path: Path) -> dict[str, Any]:
    try:
        size, suffix = path.stat().st_size, path.suffix.lower()
        _require(size > 0, "audio", "Empty audio file.")
        if suffix == ".wav":
            with wave.open(str(path), "rb") as handle:
                byte_count = handle.getnframes() * handle.getnchannels() * handle.getsampwidth()
                _require(0 < byte_count <= size, "audio", "Invalid WAV length.")
                # Confirm the declared payload exists without loading the whole file.
                handle.setpos(handle.getnframes() - 1)
                _require(len(handle.readframes(1)) == handle.getnchannels() * handle.getsampwidth(),
                         "audio", "Truncated WAV payload.")
        elif suffix == ".mp3":
            with path.open("rb") as handle:
                head = handle.read(10)
                offset = 0
                if head.startswith(b"ID3") and len(head) == 10:
                    _require(all(b < 128 for b in head[6:10]), "audio", "Invalid ID3 size.")
                    offset = 10 + sum(b << shift for b, shift in zip(head[6:10], (21, 14, 7, 0)))
                    if head[3] == 4 and head[5] & 16:
                        offset += 10
                for _ in range(2):
                    handle.seek(offset)
                    length = _mp3_frame_size(handle.read(4))
                    _require(length > 0 and offset + length <= size, "audio", "Invalid or truncated MP3 frames.")
                    offset += length
        else:
            raise ProjectError("audio", "Only local MP3 and WAV files are supported.")
        return dict(format=suffix[1:], size_bytes=size, scope="basic_structure_only")
    except (OSError, wave.Error, EOFError) as exc:
        raise ProjectError("audio", "Cannot read a valid local audio file.") from exc


def _source_page(value: str, song_id: str) -> str:
    value = _clean(value)
    try:
        url = urlsplit(value)
        valid = (url.scheme == "https" and url.hostname in ("suno.com", "www.suno.com")
                 and url.username is None and url.password is None and url.port is None
                 and not url.query and not url.fragment
                 and re.fullmatch(r"/(?:song|s)/[A-Za-z0-9_-]+/?", url.path))
    except ValueError:
        valid = False
    _require(bool(valid), "source", "Use an observed public Suno song page without credentials or query parameters.")
    if url.path.startswith("/song/"):
        _require(url.path.rstrip("/").split("/")[-1] == song_id,
                 "source", "Source page identity differs from the observed song.")
    return value


def add_candidate(root: Path, batch_id: str, song_id: str, source_file: Path,
                  source_page: str) -> dict[str, Any]:
    root, source_page = _root(root), _source_page(source_page, song_id)
    media = _media_check(source_file)
    digest = _digest(source_file)
    with _edit(root) as state:
        batch = _find(state["batches"], batch_id)
        _require(batch["submission_status"] == "submitted" and song_id in batch["song_ids"],
                 "identity", "Song was not observed in this submitted batch.")
        previous = [row for row in state["candidates"] if row["song_id"] == song_id]
        _require(all(row["batch_id"] == batch_id for row in previous),
                 "identity", "Song already belongs to another batch.")
        previous = [row for row in previous if Path(row["path"]).suffix.lower() == source_file.suffix.lower()]
        if previous:
            row = previous[0]
            _require(row["batch_id"] == batch_id and row["sha256"] == digest,
                     "identity", "Song already belongs to another batch or different file content.")
            _verify(root, row)
            return row
        identity = _id()
        relative = f"audio/{identity}{source_file.suffix.lower()}"
        target = _path(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        _require(not target.exists(), "exists", "Candidate target already exists.")
        shutil.copyfile(source_file, target)
        _require(_digest(target) == digest, "integrity", "Audio changed while copying.")
        row = dict(id=identity, batch_id=batch_id, song_id=song_id, source_page=source_page,
                   path=relative, sha256=digest, media_check=media)
        state["candidates"].append(row)
    return row


def record_review(root: Path, decision_file: Path) -> dict[str, Any]:
    root, decision = _root(root), _json(decision_file)
    _require(set(decision) <= {"notes", "selected_ids", "rejected_ids", "required_formats"} and "notes" in decision,
             "review", "Expected notes and optional selected_ids/rejected_ids.")
    notes = _clean(decision["notes"])
    with _edit(root) as state:
        selected = decision.get("selected_ids", state["final_selection"])
        rejected = decision.get("rejected_ids", [])
        for ids in (selected, rejected):
            _require(isinstance(ids, list) and all(isinstance(s, str) for s in ids),
                     "review", "Candidate identities must be a list.")
            for identity in ids:
                _verify(root, _find(state["candidates"], identity))
        _require(not set(selected) & set(rejected), "review", "Selected and rejected candidates overlap.")
        row = dict(at=_now(), notes=notes, rejected_ids=list(dict.fromkeys(rejected)))
        if "selected_ids" in decision:
            row["selected_ids"] = list(dict.fromkeys(selected))
            state["final_selection"] = row["selected_ids"][:]
        if "required_formats" in decision:
            row["required_formats"] = _formats(decision["required_formats"])
            state["required_formats"] = row["required_formats"][:]
        state["reviews"].append(row)
    return row


def archive_project(root: Path) -> dict[str, Any]:
    root = _root(root)
    identity = _id()
    temporary_rel, final_rel = f"final/.pending-{identity}", f"final/{identity}"
    try:
        with _edit(root) as state:
            _require(bool(state["final_selection"]), "review", "No explicit final selection.")
            _require(not _missing_formats(state), "formats", "Selected songs are missing required audio formats.")
            selected = [_find(state["candidates"], identity) for identity in state["final_selection"]]
            sources = {}
            for candidate in selected:
                audio = _verify(root, candidate)
                _media_check(audio)
                _source_page(candidate["source_page"], candidate["song_id"])
                sources[candidate["path"]] = (audio, candidate["sha256"])
                batch = _find(state["batches"], candidate["batch_id"])
                _require(batch["submission_status"] == "submitted" and candidate["song_id"] in batch["song_ids"],
                         "identity", "Selected audio has inconsistent batch provenance.")
                package = _verify(root, batch, "package_path", "package_sha256")
                payload = _json(package)
                _require("lyrics_id" in payload and payload["lyrics_id"] == batch["lyrics_id"]
                         and payload.get("instrumental") is (batch["lyrics_id"] is None),
                         "integrity", "Package mode or lyric identity differs from its batch.")
                sources[batch["package_path"]] = (package, batch["package_sha256"])
                if batch["lyrics_id"] is not None:
                    lyric = _find(state["lyrics"], batch["lyrics_id"])
                    lyric_path = _verify(root, lyric)
                    _require(isinstance(lyric.get("approval"), dict)
                             and lyric["approval"].get("sha256") == lyric["sha256"],
                             "approval", "Selected lyrics lack matching approval.")
                    _require(payload.get("lyrics") == _text(lyric_path),
                             "integrity", "Package lyrics differ from the approved file.")
                    sources[lyric["path"]] = (lyric_path, lyric["sha256"])
                else:
                    _require(payload.get("lyrics") == "", "integrity", "Instrumental package contains lyrics.")
            temporary, final = _path(root, temporary_rel), _path(root, final_rel)
            temporary.mkdir(parents=True)
            files = []
            for relative, (source, digest) in sources.items():
                target = _path(root, temporary_rel + "/" + relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                _require(_digest(target) == digest, "integrity", "Archive copy differs from selected content.")
                files.append(dict(path=relative, sha256=digest))
            manifest = dict(schema_version=1, project_id=state["project_id"], archive_id=identity,
                            selected_ids=state["final_selection"], candidates=selected, files=files,
                             reviews=state["reviews"], required_formats=state.get("required_formats", []), created_at=_now())
            review = "# Final selection\n\n" + "\n".join("- " + row["song_id"] for row in selected)
            review += "\n\n## Review notes\n\n" + "\n\n".join(row["notes"] for row in state["reviews"]) + "\n"
            review_path = _write_new(root, temporary_rel + "/review.md", review)
            files.append(dict(path="review.md", sha256=_digest(review_path)))
            provenance = []
            for batch_id in sorted({row["batch_id"] for row in selected}):
                batch = _find(state["batches"], batch_id)
                if batch.get("reference_id"):
                    snapshot = _snapshot(root, state, batch["reference_id"])["selection"]
                    for item in snapshot["items"]:
                        provenance.append(dict(batch_id=batch_id, library_id=snapshot["library_id"],
                            **{k: item[k] for k in ("entry_id", "revision", "sha256", "usage")}))
            if provenance:
                path = _write_new(root, temporary_rel + "/provenance.json", json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")
                files.append(dict(path="provenance.json", sha256=_digest(path)))
            _write_new(root, temporary_rel + "/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            manifest_hash = _digest(temporary / "manifest.json")
            _atomic_move(temporary, final)
            row = dict(id=identity, path=final_rel, manifest_sha256=manifest_hash,
                       selected_ids=state["final_selection"][:],
                       required_formats=state.get("required_formats", [])[:], created_at=_now())
            state["archives"].append(row)
    except ProjectError as exc:
        for relative in (temporary_rel, final_rel):
            try:
                if _path(root, relative).exists():
                    exc.residual_paths.append(relative)
            except (ProjectError, OSError):
                pass
        raise
    return row


def project_status(root: Path, *, library_root=None) -> dict[str, Any]:
    root = _root(root)
    state = _load(root)
    problems = []
    if not _path(root, state["brief_path"]).is_file():
        problems.append("brief_missing")
    for row in state["lyrics"] + state["candidates"] + state["batches"]:
        try:
            if "package_path" in row:
                _verify(root, row, "package_path", "package_sha256")
            else:
                _verify(root, row)
        except (ProjectError, OSError):
            problems.append("artifact_missing_or_changed:" + row["id"])
    for row in state["archives"]:
        try:
            folder = _path(root, row["path"])
            manifest = _verify(folder, dict(path="manifest.json", sha256=row["manifest_sha256"]))
            exported = _json(manifest)
            _fields(exported, dict(files=list, selected_ids=list, project_id=str, archive_id=str))
            _require(exported["project_id"] == state["project_id"] and exported["archive_id"] == row["id"]
                     and exported["selected_ids"] == row["selected_ids"],
                     "integrity", "Archive identity differs from its project record.")
            for item in exported["files"]:
                _fields(item, dict(path=str, sha256=str))
                _verify(folder, item)
            _require(any(item["path"] == "review.md" for item in exported["files"]),
                     "integrity", "Archive review is not checksummed; manual verification required.")
        except (ProjectError, OSError):
            problems.append("archive_missing_or_changed:" + row["id"])
    next_action = "continue_project"
    if any(b["submission_status"] == "uncertain" for b in state["batches"]):
        next_action = "reconcile_suno"
    if problems:
        next_action = "repair_files"
    missing = _missing_formats(state)
    reference_problems = []
    reference_status = "none"
    needed = {b["reference_id"] for b in state["batches"] if b.get("reference_id")
              and b["submission_status"] in ("prepared", "uncertain")}
    if state.get("active_reference_id"):
        needed.add(state["active_reference_id"])
    for row in state.get("references", []):
        try:
            _snapshot(root, state, row["id"])
        except (ProjectError, OSError):
            problems.append("reference_missing_or_changed:" + row["id"])
    if needed:
        reference_status = "unverified" if library_root is None else "valid"
        if library_root is not None:
            for ref_id in sorted(needed):
                try:
                    library = _sibling("library")
                    library.disjoint(root, library_root)
                    snapshot = _snapshot(root, state, ref_id)
                    with library.validated_selection(library_root, snapshot["selection"], "generation"):
                        pass
                except (ProjectError, OSError) as exc:
                    reference_status = "invalid"
                    reference_problems.append(dict(reference_id=ref_id, error=getattr(exc, "code", "io")))
    if problems:
        next_action = "repair_files"
    delivery_status = "not_selected"
    if state["final_selection"]:
        delivery_status = "missing_formats" if missing else "ready_to_archive"
        if not missing and any(row["selected_ids"] == state["final_selection"]
                               and row.get("required_formats", []) == state.get("required_formats", [])
                               for row in state["archives"]):
            delivery_status = "archived"
    if problems:
        delivery_status = "repair_files"
    return dict(project_id=state["project_id"], title=state["title"], problems=problems,
                reference_status=reference_status, reference_problems=reference_problems,
                active_reference_id=state.get("active_reference_id"), references=state.get("references", []),
                next_action=next_action, delivery_status=delivery_status, missing_formats=missing,
                required_formats=state.get("required_formats", []),
                lyrics=state["lyrics"], batches=state["batches"], candidates=state["candidates"],
                final_selection=state["final_selection"], archives=state["archives"])


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ProjectError("usage", "Invalid arguments; use --help for supported commands.")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    definitions = {
        "init": [("--title", str), ("--brief-file", Path)],
        "add-lyrics": [("--file", Path)],
        "approve-lyrics": [("--lyrics-id", str), ("--approval-file", Path)],
        "status": [],
        "prepare-batch": [("--package-file", Path), ("--authorization-file", Path)],
        "import-existing-batch": [("--package-file", Path), ("--authorization-file", Path), ("--observation-file", Path)],
        "record-submission": [("--batch-id", str), ("--observation-file", Path)],
        "add-candidate": [("--batch-id", str), ("--song-id", str), ("--file", Path), ("--source-page", str)],
        "review": [("--decision-file", Path)],
        "archive": [],
        "attach-references": [("--selection-file", Path)],
        "clear-references": [("--reason-file", Path)],
        "check-generation": [("--batch-id", str)],
    }
    for name, arguments in definitions.items():
        command = commands.add_parser(name)
        command.add_argument("--project", required=True, type=Path)
        if name in ("attach-references", "prepare-batch", "record-submission", "check-generation", "status"):
            command.add_argument("--library", type=Path, required=name == "attach-references")
        for flag, value_type in arguments:
            command.add_argument(flag, required=True, type=value_type)
    try:
        args = parser.parse_args(argv)
        handlers = {
            "init": lambda: init_project(args.project, args.title, args.brief_file),
            "add-lyrics": lambda: add_lyrics(args.project, args.file),
            "approve-lyrics": lambda: approve_lyrics(args.project, args.lyrics_id, args.approval_file),
            "status": lambda: project_status(args.project, library_root=args.library),
            "prepare-batch": lambda: prepare_batch(args.project, args.package_file, args.authorization_file, library_root=args.library),
            "import-existing-batch": lambda: import_existing_batch(args.project, args.package_file, args.authorization_file, args.observation_file),
            "record-submission": lambda: record_submission(args.project, args.batch_id, args.observation_file, library_root=args.library),
            "add-candidate": lambda: add_candidate(args.project, args.batch_id, args.song_id, args.file, args.source_page),
            "review": lambda: record_review(args.project, args.decision_file),
            "archive": lambda: archive_project(args.project),
            "attach-references": lambda: attach_references(args.project, args.library, args.selection_file),
            "clear-references": lambda: clear_references(args.project, args.reason_file),
            "check-generation": lambda: check_generation(args.project, args.batch_id, args.library),
        }
        print(json.dumps(handlers[args.command](), ensure_ascii=False))
        return 0
    except ProjectError as exc:
        error = dict(error=exc.code, message=str(exc))
        if exc.residual_paths:
            error["residual_paths"] = exc.residual_paths
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, UnicodeError) as exc:
        print(json.dumps(dict(error="io", message="Cannot access project files.")), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
