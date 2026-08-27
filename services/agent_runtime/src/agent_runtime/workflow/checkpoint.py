"""SQLite checkpoint store for a single, verified Runtime instance.

The store persists only validated orchestration state.  It intentionally has no
capability, cookie, credential, prompt, or ERP response fields.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from agent_runtime.agent.contracts import canonical_json
from agent_runtime.workflow.contracts import WorkflowState, parse_deadline

WORKFLOW_DB_PATH_ENV = "SYNORA_WORKFLOW_DB_PATH"
CHECKPOINT_SCHEMA_VERSION = "1"
_SENSITIVE_KEY = re.compile(
    r"(?:capability|authorization|cookie|password|passwd|secret|api[_-]?key|bearer|token|prompt)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(?:api[_-]?key|bearer|token|secret|password|passwd|capability|authorization|cookie)\b"
    r"\s*[:=]\s*\S+"
)


class CheckpointError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class CheckpointConflict(CheckpointError):
    def __init__(self, detail: str = "checkpoint changed concurrently") -> None:
        super().__init__("CHECKPOINT_CONFLICT", detail)


class CheckpointIncompatible(CheckpointError):
    def __init__(self, detail: str = "checkpoint is incompatible") -> None:
        super().__init__("CHECKPOINT_INCOMPATIBLE", detail)


class CheckpointUnavailable(CheckpointError):
    def __init__(self, detail: str = "workflow checkpoint storage is unavailable") -> None:
        super().__init__("CHECKPOINT_UNAVAILABLE", detail)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def _safe_payload(value: object) -> None:
    if isinstance(value, str):
        if _SENSITIVE_TEXT.search(value):
            raise CheckpointIncompatible("checkpoint contains a secret-like value")
    elif isinstance(value, list | tuple):
        for item in value:
            _safe_payload(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                raise CheckpointIncompatible("checkpoint contains a forbidden field")
            _safe_payload(item)


class SQLiteCheckpointStore:
    """Durable CAS/checkpoint storage with a short-lived exclusive lease."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
        lease_seconds: int = 30,
    ) -> None:
        configured = path if path is not None else os.environ.get(WORKFLOW_DB_PATH_ENV, "").strip()
        if not configured:
            raise CheckpointUnavailable(f"{WORKFLOW_DB_PATH_ENV} is required")
        if lease_seconds < 1 or lease_seconds > 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        self.path = Path(configured).expanduser()
        if self.path.exists() and self.path.is_dir():
            raise CheckpointUnavailable("workflow database path is a directory")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lease_seconds = lease_seconds
        self._initialize()

    @classmethod
    def from_environment(cls, *, clock: Callable[[], datetime] = _utc_now) -> SQLiteCheckpointStore:
        return cls(None, clock=clock)

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=5.0,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            return connection
        except sqlite3.Error as exc:
            raise CheckpointUnavailable("workflow database cannot be opened") from exc

    def _initialize(self) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                        run_id TEXT PRIMARY KEY,
                        revision INTEGER NOT NULL,
                        schema_version TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        status TEXT NOT NULL,
                        lease_id TEXT,
                        lease_expires_at TEXT,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS workflow_checkpoint_history (
                        run_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        saved_at TEXT NOT NULL,
                        PRIMARY KEY (run_id, revision),
                        FOREIGN KEY (run_id) REFERENCES workflow_checkpoints(run_id)
                    );
                    """
                )
            os.chmod(self.path, 0o600)
            # SQLite WAL creates a sidecar file; keep both private as well.
            for sidecar in (Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
                if sidecar.exists():
                    os.chmod(sidecar, 0o600)
        except (OSError, sqlite3.Error) as exc:
            raise CheckpointUnavailable("workflow database initialization failed") from exc

    def create(self, state: WorkflowState, *, lease_id: str | None = None) -> None:
        payload = self._encode(state)
        now = _iso(self._clock())
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO workflow_checkpoints
                    (run_id, revision, schema_version, payload, status, lease_id,
                     lease_expires_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(state.run_id),
                        state.revision,
                        CHECKPOINT_SCHEMA_VERSION,
                        payload,
                        state.status,
                        lease_id,
                        self._lease_expiry(lease_id),
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO workflow_checkpoint_history VALUES (?, ?, ?, ?)",
                    (str(state.run_id), state.revision, state.status, now),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise CheckpointConflict("checkpoint already exists") from exc
        except sqlite3.Error as exc:
            raise CheckpointUnavailable("checkpoint create failed") from exc

    def load(self, run_id: UUID | str) -> WorkflowState:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT schema_version, payload FROM workflow_checkpoints WHERE run_id = ?",
                    (str(run_id),),
                ).fetchone()
        except sqlite3.Error as exc:
            raise CheckpointUnavailable("checkpoint load failed") from exc
        if row is None:
            raise CheckpointError("CHECKPOINT_NOT_FOUND", "checkpoint is not available")
        if row["schema_version"] != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointIncompatible("unknown checkpoint schema version")
        try:
            decoded = json.loads(row["payload"])
            _safe_payload(decoded)
            if not isinstance(decoded, dict) or decoded.get("schema_version") != "1":
                raise ValueError
            return WorkflowState.model_validate_json(canonical_json(decoded))
        except CheckpointError:
            raise
        except Exception as exc:
            raise CheckpointIncompatible("checkpoint payload is invalid") from exc

    def save(
        self,
        state: WorkflowState,
        *,
        expected_revision: int,
        lease_id: str | None = None,
        keep_lease: bool = False,
    ) -> None:
        if state.revision != expected_revision + 1:
            raise CheckpointConflict("state revision must increment exactly once")
        payload = self._encode(state)
        now = _iso(self._clock())
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT revision, lease_id, lease_expires_at
                    FROM workflow_checkpoints WHERE run_id = ?
                    """,
                    (str(state.run_id),),
                ).fetchone()
                if row is None:
                    raise CheckpointError("CHECKPOINT_NOT_FOUND", "checkpoint is not available")
                if int(row["revision"]) != expected_revision:
                    raise CheckpointConflict()
                if not self._lease_matches(row, lease_id):
                    raise CheckpointConflict("checkpoint lease is not held")
                connection.execute(
                    """
                    UPDATE workflow_checkpoints
                    SET revision = ?, schema_version = ?, payload = ?, status = ?,
                        lease_id = ?, lease_expires_at = ?, updated_at = ?
                    WHERE run_id = ? AND revision = ?
                    """,
                    (
                        state.revision,
                        CHECKPOINT_SCHEMA_VERSION,
                        payload,
                        state.status,
                        lease_id if keep_lease else None,
                        self._lease_expiry(lease_id) if keep_lease else None,
                        now,
                        str(state.run_id),
                        expected_revision,
                    ),
                )
                connection.execute(
                    "INSERT INTO workflow_checkpoint_history VALUES (?, ?, ?, ?)",
                    (str(state.run_id), state.revision, state.status, now),
                )
                connection.commit()
        except CheckpointError:
            raise
        except sqlite3.Error as exc:
            raise CheckpointUnavailable("checkpoint save failed") from exc

    def acquire_lease(self, run_id: UUID | str, *, expected_revision: int) -> str:
        lease_id = secrets.token_urlsafe(18)
        now = self._clock()
        expiry = _iso(now + timedelta(seconds=self._lease_seconds))
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT revision, lease_id, lease_expires_at "
                    "FROM workflow_checkpoints WHERE run_id = ?",
                    (str(run_id),),
                ).fetchone()
                if row is None:
                    raise CheckpointError("CHECKPOINT_NOT_FOUND", "checkpoint is not available")
                if int(row["revision"]) != expected_revision:
                    raise CheckpointConflict()
                if row["lease_id"] and row["lease_expires_at"]:
                    try:
                        active = parse_deadline(str(row["lease_expires_at"])) > now
                    except ValueError:
                        active = True
                    if active:
                        raise CheckpointConflict("checkpoint lease is active")
                connection.execute(
                    "UPDATE workflow_checkpoints "
                    "SET lease_id = ?, lease_expires_at = ? "
                    "WHERE run_id = ? AND revision = ?",
                    (lease_id, expiry, str(run_id), expected_revision),
                )
                connection.commit()
        except CheckpointError:
            raise
        except sqlite3.Error as exc:
            raise CheckpointUnavailable("checkpoint lease acquisition failed") from exc
        return lease_id

    def release_lease(self, run_id: UUID | str, lease_id: str) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    "UPDATE workflow_checkpoints "
                    "SET lease_id = NULL, lease_expires_at = NULL "
                    "WHERE run_id = ? AND lease_id = ?",
                    (str(run_id), lease_id),
                )
        except sqlite3.Error as exc:
            raise CheckpointUnavailable("checkpoint lease release failed") from exc

    def recoverable(self) -> Iterator[WorkflowState]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT run_id, schema_version, payload, lease_id, lease_expires_at "
                    "FROM workflow_checkpoints "
                    "WHERE status IN ('READY', 'RUNNING', 'INTERRUPTED') "
                    "ORDER BY updated_at"
                ).fetchall()
        except sqlite3.Error as exc:
            raise CheckpointUnavailable("checkpoint scan failed") from exc
        now = self._clock()
        for row in rows:
            if row["lease_id"] and row["lease_expires_at"]:
                try:
                    if parse_deadline(str(row["lease_expires_at"])) > now:
                        continue
                except ValueError:
                    continue
            if row["schema_version"] != CHECKPOINT_SCHEMA_VERSION:
                continue
            try:
                decoded = json.loads(row["payload"])
                _safe_payload(decoded)
                yield WorkflowState.model_validate_json(canonical_json(decoded))
            except Exception:
                # Startup recovery never guesses about malformed state.
                continue

    def history(self, run_id: UUID | str) -> list[dict[str, object]]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT revision, status, saved_at "
                    "FROM workflow_checkpoint_history WHERE run_id = ? ORDER BY revision",
                    (str(run_id),),
                ).fetchall()
        except sqlite3.Error as exc:
            raise CheckpointUnavailable("checkpoint history load failed") from exc
        return [dict(row) for row in rows]

    def _encode(self, state: WorkflowState) -> str:
        value = state.model_dump(mode="json")
        _safe_payload(value)
        try:
            return canonical_json(value)
        except (TypeError, ValueError) as exc:
            raise CheckpointIncompatible("checkpoint state is not JSON safe") from exc

    def _lease_expiry(self, lease_id: str | None) -> str | None:
        if lease_id is None:
            return None
        return _iso(self._clock() + timedelta(seconds=self._lease_seconds))

    def _lease_matches(self, row: sqlite3.Row, lease_id: str | None) -> bool:
        stored = row["lease_id"]
        if stored is None:
            return lease_id is None
        if lease_id != stored:
            return False
        expiry = row["lease_expires_at"]
        if not expiry:
            return False
        try:
            return parse_deadline(str(expiry)) > self._clock()
        except ValueError:
            return False
