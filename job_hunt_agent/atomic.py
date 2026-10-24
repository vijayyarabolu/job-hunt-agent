"""Atomic file writes. Each write goes to its own temporary file beside the target (a unique name, so two writers
of the same file never share one) and then os.replace()s it, so a reader sees the old file or the new one, never
half of either. The temporary file is removed when anything fails."""
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def replacing(target, mode: int | None = None):
    """Yields a fresh temporary Path in target's folder; when the block ends without an error it becomes target.
    For writers that need a file name (sqlite3 backups, tarfile). mode: the file's permissions (mkstemp makes 0600)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(name)
    try:
        yield tmp
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def write_bytes(target, data: bytes, mode: int | None = None) -> None:
    with replacing(target, mode) as tmp:
        tmp.write_bytes(data)


def write_text(target, text: str, mode: int | None = None) -> None:
    write_bytes(target, text.encode("utf-8"), mode)
