"""
LocalStorageProvider — file-system storage for development and testing.

Stores ServiceRecord JSON as local files. URLs use the file:// scheme.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

from ..exceptions import StorageError
from .interface import StorageProvider

logger = logging.getLogger(__name__)


class LocalStorageProvider(StorageProvider):
    def __init__(self, base_dir: str = ".agent-data"):
        self._base = Path(base_dir)
        try:
            self._base.mkdir(parents=True, exist_ok=True)
            # Restrict directory permissions to owner only (rwx------)
            os.chmod(self._base, stat.S_IRWXU)
        except OSError as e:
            raise StorageError(f"Failed to create storage directory '{base_dir}': {e}") from e

    async def upload(self, data: dict, filename: str | None = None) -> str:
        return self.save_sync(data, filename)

    def save_sync(self, data: dict, filename: str | None = None) -> str:
        """Synchronous save — usable from non-async contexts."""
        try:
            content = json.dumps(data, sort_keys=True, separators=(",", ":"))

            # Use provided filename or generate from job.id or hash
            if filename:
                fname = filename if filename.endswith(".json") else f"{filename}.json"
            else:
                job_data = data.get("job", {})
                job_id = job_data.get("id") if isinstance(job_data, dict) else None
                if job_id:
                    fname = f"job-{job_id}.json"
                else:
                    hash_hex = self.compute_hash(data).hex()
                    fname = f"{hash_hex}.json"

            filepath = self._base / fname
            filepath.write_text(content, encoding="utf-8")
            # Restrict file permissions to owner only (rw-------)
            os.chmod(filepath, stat.S_IRUSR | stat.S_IWUSR)
            logger.info(f"[LocalStorageProvider] Saved to {filepath}")
            return f"file://{filepath.resolve()}"
        except OSError as e:
            raise StorageError(f"Failed to save file: {e}") from e
        except (TypeError, ValueError) as e:
            raise StorageError(f"Failed to serialize data to JSON: {e}") from e

    async def download(self, url: str) -> dict:
        path = self._url_to_path(url)
        try:
            content = Path(path).read_text(encoding="utf-8")
            return json.loads(content)
        except FileNotFoundError:
            raise StorageError(f"File not found: {path}") from None
        except OSError as e:
            raise StorageError(f"Failed to read file '{path}': {e}") from e
        except json.JSONDecodeError as e:
            raise StorageError(f"Invalid JSON in file '{path}': {e}") from e

    async def exists(self, url: str) -> bool:
        path = self._url_to_path(url)
        try:
            return os.path.isfile(path)
        except OSError as e:
            logger.warning(f"Error checking file existence for '{path}': {e}")
            return False

    def _url_to_path(self, url: str) -> str:
        raw = url[7:] if url.startswith("file://") else url
        resolved = Path(raw).resolve()
        base_resolved = self._base.resolve()
        if not resolved.is_relative_to(base_resolved):
            raise StorageError("Path traversal blocked: path is outside storage directory")
        return str(resolved)
