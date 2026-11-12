"""Small shared file primitives; no runtime configuration or network access."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

MAX_TEXT_BYTES = 2 * 1024 * 1024
SECRET = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9_]{20,}|"
    r"github_pat_[a-z0-9_]{20,})\b|"
    r"\b(?:api[_ -]?key|app[_ -]?secret|access[_ -]?token|endpoint[_ -]?id)"
    r"\s*[:=]\s*\S+"
)


class RecordError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.residual_paths = []


def require(condition, code, message):
    if not condition:
        raise RecordError(code, message)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id():
    return str(uuid.uuid4())


def clean(value, *, empty=False):
    require(isinstance(value, str), "input", "Expected text.")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    require(empty or bool(value.strip()), "input", "Text must not be empty.")
    require(not SECRET.search(value), "secret", "Potential credential detected; input not stored.")
    return value


def read_text(path):
    try:
        require(path.stat().st_size <= MAX_TEXT_BYTES, "input", "Text file too large.")
        with path.open("rb") as handle:
            content = handle.read(MAX_TEXT_BYTES + 1)
        require(len(content) <= MAX_TEXT_BYTES, "input", "Text file too large.")
        return clean(content.decode("utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        raise RecordError("input", "Cannot read UTF-8 input file.") from exc


def read_json(path):
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise RecordError("json", "Invalid JSON file.") from exc
    require(isinstance(data, dict), "json", "Expected a JSON object.")
    return data


def root(path):
    return Path(path).resolve()


def inside(root, relative):
    require(isinstance(relative, str) and bool(relative), "path", "Invalid record path.")
    rel = Path(relative)
    require(not rel.is_absolute() and ".." not in rel.parts and ":" not in relative,
            "path", "Record paths must stay inside their root.")
    target = (root / rel).resolve()
    require(target != root and target.is_relative_to(root), "path", "Record path escapes its root.")
    return target


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def atomic_move(source, target, *, replace=False):
    operation = os.replace if replace else os.rename
    for delay in (0.05, 0.1, 0.2, 0.4, None):
        try:
            operation(source, target)
            return
        except PermissionError as exc:
            if sys.platform != "win32" or getattr(exc, "winerror", None) not in (5, 32, 33) or delay is None:
                raise
            time.sleep(delay)


def write_new(root, relative, text):
    require(len(text.encode("utf-8")) <= MAX_TEXT_BYTES, "size", "Text file too large.")
    path = inside(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def atomic_json(root, relative, data):
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    require(len(serialized.encode("utf-8")) <= MAX_TEXT_BYTES,
            "size", "Record limit reached; previous manifest preserved.")
    target = inside(root, relative)
    temporary = inside(root, ".record-" + new_id() + ".tmp")
    try:
        write_new(root, temporary.name, serialized)
        atomic_move(temporary, target, replace=True)
    finally:
        temporary.unlink(missing_ok=True)
