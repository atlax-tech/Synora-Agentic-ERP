"""Runtime-local SQLite persistence for governed non-Working Memory.

This adapter is deliberately limited to the single-instance development
boundary.  It stores complete validated ``MemoryRecord`` payloads and exposes
no search, authorization, ERP, or generic SQL operations.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import ValidationError

from agent_runtime.memory.contracts import MemoryRecord
from agent_runtime.memory.persistence import (
    AtomicCorrectionCommand,
    CandidateInsertCommand,
    MemoryPersistenceError,
    MemoryPersistenceErrorCode,
    SingleRecordCasCommand,
)

MEMORY_DB_PATH_ENV = "SYNORA_MEMORY_DB_PATH"
MEMORY_SCHEMA_VERSION = "1"
_BUSY_TIMEOUT_MS = 5_000


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def _error(code: MemoryPersistenceErrorCode, detail: str) -> MemoryPersistenceError:
    return MemoryPersistenceError(code, detail)


class SQLiteMemoryStore:
    """Durable Memory adapter for one verified Runtime instance."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        try:
            configured = (
                os.fspath(path) if path is not None else os.environ.get(MEMORY_DB_PATH_ENV, "")
            )
        except TypeError as exc:
            raise _error("INVALID_COMMAND", "memory database path is invalid") from exc
        if isinstance(configured, bytes):
            raise _error("INVALID_COMMAND", "memory database path is invalid")
        configured = configured.strip()
        if not configured:
            raise _error("INVALID_COMMAND", f"{MEMORY_DB_PATH_ENV} is required")

        try:
            self.path = Path(configured).expanduser()
            if self.path.exists() and self.path.is_dir():
                raise _error("INVALID_COMMAND", "memory database path is invalid")
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except MemoryPersistenceError:
            raise
        except (OSError, ValueError) as exc:
            raise _error("INVALID_COMMAND", "memory database path is invalid") from exc
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                str(self.path),
                timeout=5.0,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            if not journal_mode or str(journal_mode[0]).lower() != "wal":
                connection.close()
                raise sqlite3.OperationalError("WAL mode is unavailable")
            connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise _error("ATOMIC_COMMIT_FAILED", "memory database cannot be opened") from exc

    def _initialize(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    memory_id TEXT PRIMARY KEY NOT NULL,
                    state TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        except MemoryPersistenceError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise _error("ATOMIC_COMMIT_FAILED", "memory database initialization failed") from exc
        finally:
            if connection is not None:
                connection.close()
        self._make_private()

    def _make_private(self) -> None:
        for candidate in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            try:
                if candidate.exists():
                    os.chmod(candidate, 0o600)
            except OSError:
                # Some filesystems do not support chmod; SQLite remains the
                # authority for the data and the requirement is best-effort.
                continue

    @staticmethod
    def _validate_command(command: object, expected_type: type[Any], field_name: str) -> Any:
        if not isinstance(command, expected_type):
            raise _error("INVALID_COMMAND", f"{field_name} is invalid")
        return command

    @staticmethod
    def _record_with_id(record: MemoryRecord, memory_id: str) -> MemoryRecord:
        values = dict(record.model_dump())
        values["scope"] = record.scope
        values["memory_id"] = memory_id
        try:
            return MemoryRecord(**values)
        except (ValidationError, TypeError, ValueError) as exc:
            raise _error("INVALID_COMMAND", "memory record is invalid") from exc

    @staticmethod
    def _json_uuid(value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            parsed = UUID(value)
        except ValueError:
            return value
        return parsed if str(parsed) == value else value

    @staticmethod
    def _json_datetime(value: object) -> object:
        if not isinstance(value, str) or "T" not in value:
            return value
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return value
        return parsed

    @classmethod
    def _validate_json_record(cls, payload: str) -> MemoryRecord:
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("payload is not an object")

        normalized = dict(decoded)
        for field_name in ("source_run_id",):
            normalized[field_name] = cls._json_uuid(normalized.get(field_name))

        scope = normalized.get("scope")
        if isinstance(scope, dict):
            normalized_scope = dict(scope)
            normalized_scope["run_id"] = cls._json_uuid(normalized_scope.get("run_id"))
            normalized["scope"] = normalized_scope

        for field_name in ("created_at", "expires_at", "reviewed_at"):
            normalized[field_name] = cls._json_datetime(normalized.get(field_name))

        return MemoryRecord.model_validate(normalized)

    @staticmethod
    def _serialize(record: MemoryRecord) -> str:
        try:
            payload = json.dumps(
                record.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            SQLiteMemoryStore._validate_json_record(payload)
            return payload
        except (ValidationError, TypeError, ValueError) as exc:
            raise _error("INVALID_COMMAND", "memory record is invalid") from exc

    @staticmethod
    def _deserialize(row: sqlite3.Row) -> MemoryRecord:
        try:
            if row["schema_version"] != MEMORY_SCHEMA_VERSION:
                raise ValueError("unknown schema version")
            payload = row["payload"]
            if not isinstance(payload, str):
                raise ValueError("payload is not text")
            decoded = json.loads(payload)
            if not isinstance(decoded, dict) or decoded.get("memory_id") != row["memory_id"]:
                raise ValueError("payload identity is invalid")
            record = SQLiteMemoryStore._validate_json_record(payload)
            if record.memory_id != row["memory_id"]:
                raise ValueError("payload identity is invalid")
            if record.state != row["state"] or record.state_version != row["state_version"]:
                raise ValueError("payload index is invalid")
            return record
        except (ValidationError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise _error("ATOMIC_COMMIT_FAILED", "stored memory is invalid") from exc

    @staticmethod
    def _fetch(connection: sqlite3.Connection, memory_id: str) -> sqlite3.Row | None:
        row = connection.execute(
            "SELECT memory_id, state, state_version, schema_version, payload, updated_at "
            "FROM memory_records WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass

    async def create_candidate(self, command: CandidateInsertCommand) -> MemoryRecord:
        """Insert one candidate without upsert or automatic approval."""

        candidate_command = self._validate_command(
            command, CandidateInsertCommand, "candidate command"
        )
        original = candidate_command.record
        attempts = 0
        while True:
            candidate = original
            if candidate.memory_id is None:
                candidate = self._record_with_id(candidate, str(uuid4()))
            payload = self._serialize(candidate)
            connection: sqlite3.Connection | None = None
            try:
                connection = self._connect()
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO memory_records "
                    "(memory_id, state, state_version, schema_version, payload, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        candidate.memory_id,
                        candidate.state,
                        candidate.state_version,
                        MEMORY_SCHEMA_VERSION,
                        payload,
                        _iso(_utc_now()),
                    ),
                )
                connection.commit()
                return self._validate_json_record(payload)
            except sqlite3.IntegrityError as exc:
                if connection is not None:
                    self._rollback(connection)
                if original.memory_id is not None or attempts >= 2:
                    raise _error("CONFLICT", "memory record already exists") from exc
                attempts += 1
            except MemoryPersistenceError:
                if connection is not None:
                    self._rollback(connection)
                raise
            except (OSError, sqlite3.Error) as exc:
                if connection is not None:
                    self._rollback(connection)
                raise _error("ATOMIC_COMMIT_FAILED", "memory persistence failed") from exc
            finally:
                if connection is not None:
                    connection.close()

    async def get_exact(self, memory_id: str) -> MemoryRecord:
        """Load one exact ID; this is not an authorization or recall API."""

        if not isinstance(memory_id, str) or not memory_id.strip() or len(memory_id) > 140:
            raise _error("INVALID_COMMAND", "memory ID is invalid")
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            row = self._fetch(connection, memory_id)
            if row is None:
                raise _error("NOT_FOUND", "memory record is not available")
            return self._deserialize(row)
        except MemoryPersistenceError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise _error("ATOMIC_COMMIT_FAILED", "memory load failed") from exc
        finally:
            if connection is not None:
                connection.close()

    async def commit_cas(self, command: SingleRecordCasCommand) -> MemoryRecord:
        """Commit one exact lifecycle update under snapshot and version CAS."""

        cas_command = self._validate_command(command, SingleRecordCasCommand, "CAS command")
        current = cas_command.current
        if current.memory_id is None:
            raise _error("INVALID_COMMAND", "current memory ID is required")
        payload = self._serialize(cas_command.updated)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            row = self._fetch(connection, current.memory_id)
            if row is None:
                raise _error("NOT_FOUND", "memory record is not available")
            stored = self._deserialize(row)
            if stored != current or stored.state_version != cas_command.expected_state_version:
                raise _error("STALE_VERSION", "memory record changed concurrently")
            cursor = connection.execute(
                "UPDATE memory_records SET state = ?, state_version = ?, schema_version = ?, "
                "payload = ?, updated_at = ? WHERE memory_id = ? AND state_version = ?",
                (
                    cas_command.updated.state,
                    cas_command.updated.state_version,
                    MEMORY_SCHEMA_VERSION,
                    payload,
                    _iso(_utc_now()),
                    current.memory_id,
                    cas_command.expected_state_version,
                ),
            )
            if cursor.rowcount != 1:
                raise _error("STALE_VERSION", "memory record changed concurrently")
            connection.commit()
            return self._validate_json_record(payload)
        except MemoryPersistenceError:
            if connection is not None:
                self._rollback(connection)
            raise
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                self._rollback(connection)
            raise _error("ATOMIC_COMMIT_FAILED", "memory CAS failed") from exc
        finally:
            if connection is not None:
                connection.close()

    async def commit_correction_atomic(
        self, command: AtomicCorrectionCommand
    ) -> tuple[MemoryRecord, MemoryRecord]:
        """Commit correction and supersession in one all-or-nothing transaction."""

        correction_command = self._validate_command(
            command, AtomicCorrectionCommand, "correction command"
        )
        candidate_id = correction_command.candidate_before.memory_id
        prior_id = correction_command.prior_before.memory_id
        if candidate_id is None or prior_id is None:
            raise _error("INVALID_COMMAND", "correction memory IDs are required")
        correction_payload = self._serialize(correction_command.approved_correction)
        prior_payload = self._serialize(correction_command.superseded_prior)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            candidate_row = self._fetch(connection, candidate_id)
            prior_row = self._fetch(connection, prior_id)
            if candidate_row is None or prior_row is None:
                raise _error("NOT_FOUND", "memory record is not available")
            stored_candidate = self._deserialize(candidate_row)
            stored_prior = self._deserialize(prior_row)
            if (
                stored_candidate != correction_command.candidate_before
                or stored_candidate.state_version != correction_command.expected_candidate_version
            ):
                raise _error("STALE_VERSION", "memory record changed concurrently")
            if (
                stored_prior != correction_command.prior_before
                or stored_prior.state_version != correction_command.expected_prior_version
            ):
                raise _error("STALE_VERSION", "memory record changed concurrently")

            candidate_update = connection.execute(
                "UPDATE memory_records SET state = ?, state_version = ?, schema_version = ?, "
                "payload = ?, updated_at = ? WHERE memory_id = ? AND state_version = ?",
                (
                    correction_command.approved_correction.state,
                    correction_command.approved_correction.state_version,
                    MEMORY_SCHEMA_VERSION,
                    correction_payload,
                    _iso(_utc_now()),
                    candidate_id,
                    correction_command.expected_candidate_version,
                ),
            )
            if candidate_update.rowcount != 1:
                raise _error("STALE_VERSION", "memory record changed concurrently")
            prior_update = connection.execute(
                "UPDATE memory_records SET state = ?, state_version = ?, schema_version = ?, "
                "payload = ?, updated_at = ? WHERE memory_id = ? AND state_version = ?",
                (
                    correction_command.superseded_prior.state,
                    correction_command.superseded_prior.state_version,
                    MEMORY_SCHEMA_VERSION,
                    prior_payload,
                    _iso(_utc_now()),
                    prior_id,
                    correction_command.expected_prior_version,
                ),
            )
            if prior_update.rowcount != 1:
                raise _error("STALE_VERSION", "memory record changed concurrently")
            connection.commit()
            return (
                self._validate_json_record(correction_payload),
                self._validate_json_record(prior_payload),
            )
        except MemoryPersistenceError:
            if connection is not None:
                self._rollback(connection)
            raise
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                self._rollback(connection)
            raise _error("ATOMIC_COMMIT_FAILED", "memory correction failed") from exc
        finally:
            if connection is not None:
                connection.close()


__all__ = ["MEMORY_DB_PATH_ENV", "MEMORY_SCHEMA_VERSION", "SQLiteMemoryStore"]
