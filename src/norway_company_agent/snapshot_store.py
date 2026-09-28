from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path


def save_snapshot(body: bytes, extension: str) -> str:
    """Persist immutable response bytes at a content-addressed path."""
    if not isinstance(body, bytes):
        raise TypeError("Snapshot body must be bytes")
    suffix = extension.lstrip(".").casefold()
    if not re.fullmatch(r"[a-z0-9]{1,10}", suffix):
        raise ValueError("Snapshot extension must be 1-10 alphanumeric characters")

    digest = hashlib.sha256(body).hexdigest()
    root = Path(os.environ.get("SIGNALPOST_SNAPSHOT_DIR", "out/snapshots"))
    target = root / digest[:2] / f"{digest}.{suffix}"
    if target.exists():
        return str(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{digest}.", suffix=".tmp", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(body)
        os.replace(temporary_path, target)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return str(target)