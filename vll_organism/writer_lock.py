"""Exclusive writer lease for organism state.

Status is read-only and query feedback uses the SQLite stimuli queue, so only
processes that construct an :class:`Organism` need this lock.  The OS releases
it automatically if a process crashes, preventing stale lock ownership.
"""
from __future__ import annotations

import os
from typing import BinaryIO, Optional


class WriterLockError(RuntimeError):
    """Raised when another organism writer already owns the database."""


class WriterLock:
    def __init__(self, db_path: str):
        self.path = os.path.abspath(db_path) + ".writer.lock"
        self._handle: Optional[BinaryIO] = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            _lock_nonblocking(handle)
        except OSError as exc:
            handle.close()
            raise WriterLockError(
                f"database {self.path[:-12]!r} already has an active organism writer; "
                "use the running watch daemon for ingestion, or stop it before running perturb"
            ) from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            _unlock(handle)
        finally:
            handle.close()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass


def _lock_nonblocking(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = ["WriterLock", "WriterLockError"]
