"""Atomic local storage for raw RFC 822 messages."""
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class StoredRawMail:
    path: str
    sha256: str
    size_bytes: int


class RawMailSpool:
    def __init__(self, root, clock=None):
        self.root = Path(root).resolve()
        self.clock = clock or datetime.now

    def _validated_path(self, value) -> Path:
        path = Path(value).resolve()
        if path == self.root or self.root not in path.parents:
            raise ValueError("邮件文件不在原始邮件暂存目录内")
        return path

    def store(self, job_id: str, raw_bytes: bytes) -> StoredRawMail:
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(job_id))
        month_dir = self.root / self.clock().strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)
        target = month_dir / f"{safe_id}.eml"
        temporary = month_dir / f".{safe_id}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(raw_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return StoredRawMail(
            str(target.resolve()), hashlib.sha256(raw_bytes).hexdigest(), len(raw_bytes)
        )

    def read(self, stored_path) -> bytes:
        return self._validated_path(stored_path).read_bytes()

    def discard(self, stored_path) -> bool:
        path = self._validated_path(stored_path)
        if not path.exists():
            return False
        path.unlink()
        return True

    def cleanup_orphans(self, referenced_paths, grace_seconds: int) -> list[str]:
        referenced = {str(self._validated_path(path)) for path in referenced_paths}
        cutoff = self.clock().timestamp() - max(0, int(grace_seconds))
        deleted = []
        if not self.root.exists():
            return deleted
        for path in sorted(self.root.rglob("*.eml")):
            resolved = path.resolve()
            if str(resolved) in referenced or resolved.stat().st_mtime > cutoff:
                continue
            resolved.unlink()
            deleted.append(str(resolved))
        return deleted
