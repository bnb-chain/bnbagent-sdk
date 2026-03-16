"""
LocalStorageProvider — file-system storage for development and testing.

Stores ServiceRecord JSON as local files. URLs use the file:// scheme.
"""

import json
import logging
import os
import stat
from pathlib import Path
from typing import Optional

from .interface import IStorageProvider
from ..exceptions import StorageError

logger = logging.getLogger(__name__)


class LocalStorageProvider(IStorageProvider):
    def __init__(self, base_dir: str = ".storage"):
        self._base = Path(base_dir)
        try:
            self._base.mkdir(parents=True, exist_ok=True)
            # Restrict directory permissions to owner only (rwx------)
            os.chmod(self._base, stat.S_IRWXU)
        except OSError as e:
            raise StorageError(f"Failed to create storage directory '{base_dir}': {e}")

    async def upload(self, data: dict, filename: Optional[str] = None) -> str:
        return self.save_sync(data, filename)

    def save_sync(self, data: dict, filename: Optional[str] = None) -> str:
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
        except (OSError, IOError) as e:
            raise StorageError(f"Failed to save file: {e}")
        except (TypeError, ValueError) as e:
            raise StorageError(f"Failed to serialize data to JSON: {e}")

    async def download(self, url: str) -> dict:
        path = self._url_to_path(url)
        try:
            content = Path(path).read_text(encoding="utf-8")
            return json.loads(content)
        except FileNotFoundError:
            raise StorageError(f"File not found: {path}")
        except (OSError, IOError) as e:
            raise StorageError(f"Failed to read file '{path}': {e}")
        except json.JSONDecodeError as e:
            raise StorageError(f"Invalid JSON in file '{path}': {e}")

    async def exists(self, url: str) -> bool:
        path = self._url_to_path(url)
        try:
            return os.path.isfile(path)
        except OSError as e:
            logger.warning(f"Error checking file existence for '{path}': {e}")
            return False

    @staticmethod
    def _url_to_path(url: str) -> str:
        if url.startswith("file://"):
            return url[7:]
        return url
