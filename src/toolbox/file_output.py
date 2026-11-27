"""L5: publish a bounded byte file inside a caller-owned root, without replacing data.

No persistent index or schema. The caller owns the destination and retention;
this module only writes a new file and removes its own temporary output.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path, PureWindowsPath


def create_bytes(path: Path, data: bytes, *, root: Path,
                 max_bytes: int) -> Path:
    """Publish a complete new file; an existing destination is never overwritten."""
    if not isinstance(data, bytes) or not data or len(data) > max_bytes:
        raise ValueError("invalid output size")
    root = Path(root).resolve(strict=True)
    path = Path(path)
    if (not path.name or PureWindowsPath(path.name).is_reserved()
            or path.name.endswith((".", " ")) or ":" in path.name):
        raise ValueError("invalid output name")
    parent = path.parent.resolve(strict=True)
    parent.relative_to(root)
    target = parent / path.name
    # lexists includes a dangling symlink. Publication below checks again atomically.
    if os.path.lexists(target):
        raise FileExistsError("output exists")
    fd, temporary = tempfile.mkstemp(prefix=".fmjj-output-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        parent.resolve(strict=True).relative_to(root)
        if os.name == "nt":
            # Windows rename fails if the destination exists; it does not replace it.
            os.rename(temporary, target)
        else:
            os.link(temporary, target)
        return target
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
