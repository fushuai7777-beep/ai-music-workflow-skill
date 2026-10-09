"""Local project records. No network, model, or browser dependencies."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any
from urllib.parse import urlsplit
import uuid
import wave


SKILL_ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_BYTES = 2 * 1024 * 1024
SECRET = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9_]{20,}|"
    r"github_pat_[a-z0-9_]{20,})\b|"
    r"\b(?:api[_ -]?key|app[_ -]?secret|access[_ -]?token|endpoint[_ -]?id)"
    r"\s*[:=]\s*\S+"
)


class ProjectError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.residual_paths: list[str] = []


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ProjectError(code, message)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id() -> str:
    return str(uuid.uuid4())


def _clean(value: str, *, empty: bool = False) -> str:
    _require(isinstance(value, str), "input", "Expected text.")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    _require(empty or bool(value.strip()), "input", "Text must not be empty.")
    _require(not SECRET.search(value), "secret", "Potential credential detected; input not stored.")
    return value


def _text(path: Path) -> str:
    try:
        _require(path.stat().st_size <= MAX_TEXT_BYTES, "input", "Text file too large.")
        return _clean(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        raise ProjectError("input", "Cannot read UTF-8 input file.") from exc


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(_text(path))
    except json.JSONDecodeError as exc:
        raise ProjectError("json", "Invalid JSON file.") from exc
    _require(isinstance(data, dict), "json", "Expected a JSON object.")
    return data


def _root(root: Path) -> Path:
    root = Path(root).resolve()
    _require(not root.is_relative_to(SKILL_ROOT), "path", "Project must be outside the Skill directory.")
    return root


def _path(root: Path, relative: str) -> Path:
    _require(isinstance(relative, str) and bool(relative), "path", "Invalid project path.")
    rel = Path(relative)
    _require(not rel.is_absolute() and ".." not in rel.parts and ":" not in relative,
             "path", "Project paths must stay inside the project.")
    target = (root / rel).resolve()
    _require(target != root and target.is_relative_to(root), "path", "Project path escapes its root.")
    return target


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


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


def _atomic_move(source: Path, target: Path, *, replace: bool = False) -> None:
    operation = os.replace if replace else os.rename
    # Windows readers can briefly deny rename/delete while allowing ordinary reads.
    for delay in (0.05, 0.1, 0.2, 0.4, None):
        try:
            operation(source, target)
            return
        except PermissionError as exc:
            if sys.platform != "win32" or getattr(exc, "winerror", None) not in (5, 32, 33) or delay is None:
                raise
            time.sleep(delay)


def _save(root: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    _require(len(serialized.encode("utf-8")) <= MAX_TEXT_BYTES,
             "size", "Project record limit reached; previous manifest preserved.")
    target = _path(root, "project.json")
    temporary = _path(root, ".project-" + _id() + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_move(temporary, target, replace=True)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _edit(root: Path, initial: dict[str, Any] | None = None):
    lock = _path(root, ".skill-project.lock")
    token = _id()
    try:
        with lock.open("x", encoding="ascii") as handle:
            handle.write(token)
    except FileExistsError as exc:
        raise ProjectError("locked", "Project is locked; verify the existing writer before recovery.") from exc
    try:
        data = _load(root) if initial is None else initial
        yield data
        _save(root, data)
    except OSError as exc:
        raise ProjectError("io", "File operation failed; project completion was not recorded.") from exc
    finally:
        if lock.exists() and lock.read_text(encoding="ascii") == token:
            lock.unlink()


def _write_new(root: Path, relative: str, text: str) -> Path:
    path = _path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


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


def prepare_batch(root: Path, package_file: Path, authorization_file: Path) -> dict[str, Any]:
    return _create_batch(root, package_file, authorization_file)


def import_existing_batch(root: Path, package_file: Path, authorization_file: Path,
                          observation_file: Path) -> dict[str, Any]:
    observation = _observation(observation_file)
    _require(observation["status"] == "submitted", "observation", "Import requires an already observed result.")
    return _create_batch(root, package_file, authorization_file, observation)


def _create_batch(root: Path, package_file: Path, authorization_file: Path,
                  observation: dict[str, Any] | None = None) -> dict[str, Any]:
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
    with _edit(root) as state:
        if observation is None:
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


def record_submission(root: Path, batch_id: str, observation_file: Path) -> dict[str, Any]:
    root, observation = _root(root), _observation(observation_file)
    status, ids, evidence = observation["status"], observation["song_ids"], observation["evidence"]
    with _edit(root) as state:
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
        row["submission_status"], row["song_ids"] = status, ids
        _append_evidence(row, status, evidence)
    return row


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


def project_status(root: Path) -> dict[str, Any]:
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
    }
    for name, arguments in definitions.items():
        command = commands.add_parser(name)
        command.add_argument("--project", required=True, type=Path)
        for flag, value_type in arguments:
            command.add_argument(flag, required=True, type=value_type)
    try:
        args = parser.parse_args(argv)
        handlers = {
            "init": lambda: init_project(args.project, args.title, args.brief_file),
            "add-lyrics": lambda: add_lyrics(args.project, args.file),
            "approve-lyrics": lambda: approve_lyrics(args.project, args.lyrics_id, args.approval_file),
            "status": lambda: project_status(args.project),
            "prepare-batch": lambda: prepare_batch(args.project, args.package_file, args.authorization_file),
            "import-existing-batch": lambda: import_existing_batch(args.project, args.package_file, args.authorization_file, args.observation_file),
            "record-submission": lambda: record_submission(args.project, args.batch_id, args.observation_file),
            "add-candidate": lambda: add_candidate(args.project, args.batch_id, args.song_id, args.file, args.source_page),
            "review": lambda: record_review(args.project, args.decision_file),
            "archive": lambda: archive_project(args.project),
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
