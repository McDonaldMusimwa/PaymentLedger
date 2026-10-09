"""Original invoice files. Local disk for now; swap for S3 when deployed."""

import hashlib
import uuid
from pathlib import Path

from app.config import settings


def save(org_id: uuid.UUID, data: bytes, suffix: str = ".pdf") -> tuple[str, str]:
    """Store the file and return (key, sha256)."""
    key = f"{org_id}/{uuid.uuid4()}{suffix}"
    path = Path(settings.storage_dir) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key, hashlib.sha256(data).hexdigest()


def path_for(key: str) -> Path:
    return Path(settings.storage_dir) / key
