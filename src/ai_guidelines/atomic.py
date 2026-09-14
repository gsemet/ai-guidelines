"""Small filesystem primitives used by manifest and lockfile persistence."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import portalocker


class AtomicWriteError(OSError):
    """Raised when a temporary file cannot be safely replaced."""


def atomic_write(path: Path | str, content: str | bytes) -> None:
    """Write content through a same-directory temporary file and replace the destination.

    Args:
        path:
            Destination file.
        content:
            UTF-8 text or bytes to publish.

    Raises:
        AtomicWriteError: If writing, flushing, or replacement fails.

    Examples:
        >>> atomic_write("/tmp/ai-guidelines-example.txt", "hello\\n")
    """
    destination = Path(path)
    temporary_name: str | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
        else:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
        if temporary_name is None:
            raise AtomicWriteError("could not atomically replace the guideline file")
        os.replace(temporary_name, destination)
        temporary_name = None
    except (OSError, TypeError) as exc:
        raise AtomicWriteError("could not atomically replace the guideline file") from exc
    finally:
        if temporary_name is not None:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name)


def atomic_write_text(path: Path | str, content: str) -> None:
    """Write UTF-8 text through :func:`atomic_write`.

    Args:
        path:
            Destination file.
        content:
            Text to publish as UTF-8.
    """
    atomic_write(path, content)


@contextmanager
def advisory_lock(path: Path | str, timeout: float = 30.0) -> Iterator[None]:
    """Hold an exclusive inter-process lock for one operation.

    The marker file is intentionally retained after the context exits.  The
    operating-system lock is released with the file descriptor, while deleting
    the pathname could let concurrent processes lock different inodes.

    Args:
        path:
            Lock-file path.
        timeout:
            Seconds to wait for another operation.

    Raises:
        TimeoutError: If the lock cannot be acquired before ``timeout``.
        OSError: If the lock file cannot be created.
    """
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with portalocker.Lock(str(lock_path), mode="a+", timeout=timeout):
            yield
    except portalocker.exceptions.LockException as exc:
        raise TimeoutError("could not acquire the guideline operation lock") from exc


operation_lock = advisory_lock
