"""
Storage providers for ServiceRecord persistence.

V1 implements LocalStorageProvider (local filesystem).
V2 can add IPFSStorageProvider (Pinata/Infura).
"""

import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .service_record import ServiceRecord


class StorageProvider(ABC):
    """Abstract storage provider interface."""

    @abstractmethod
    def save(self, record: ServiceRecord) -> str:
        """Save a ServiceRecord and return its URL (file://, ipfs://, etc.)."""
        ...

    @abstractmethod
    def load(self, url: str) -> ServiceRecord:
        """Load a ServiceRecord from a URL."""
        ...


class LocalStorageProvider(StorageProvider):
    """Save ServiceRecords to local filesystem."""

    def __init__(self, base_dir: str = ".storage"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, record: ServiceRecord) -> str:
        d = record.to_dict()
        if hasattr(record, "_hashes"):
            d["hashes"] = record._hashes
        canonical = json.dumps(d, sort_keys=True, separators=(",", ":"))
        filename = f"job_{record.job_id}_{int(time.time())}.json"
        filepath = self.base_dir / filename
        filepath.write_text(canonical)
        return f"file://{filepath.resolve()}"

    def load(self, url: str) -> ServiceRecord:
        path = url.replace("file://", "")
        data = json.loads(Path(path).read_text())
        return ServiceRecord.from_dict(data)
