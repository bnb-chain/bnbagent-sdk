"""
LocalStorageProvider — file-system storage for development and testing.

Stores ServiceRecord JSON as local files. URLs use the file:// scheme.
"""

import json
import os
from pathlib import Path

from .interface import IStorageProvider


class LocalStorageProvider(IStorageProvider):
    def __init__(self, base_dir: str = ".storage"):
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    async def upload(self, data: dict) -> str:
        return self.save_sync(data)

    def save_sync(self, data: dict) -> str:
        """Synchronous save — usable from non-async contexts."""
        content = json.dumps(data, sort_keys=True, separators=(",", ":"))
        hash_hex = self.compute_hash(data).hex()
        filename = f"{hash_hex}.json"
        filepath = self._base / filename
        filepath.write_text(content, encoding="utf-8")
        return f"file://{filepath.resolve()}"

    async def download(self, url: str) -> dict:
        path = self._url_to_path(url)
        content = Path(path).read_text(encoding="utf-8")
        return json.loads(content)

    async def exists(self, url: str) -> bool:
        path = self._url_to_path(url)
        return os.path.isfile(path)

    @staticmethod
    def _url_to_path(url: str) -> str:
        if url.startswith("file://"):
            return url[7:]
        return url
