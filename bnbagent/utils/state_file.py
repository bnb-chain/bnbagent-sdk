"""
State File Utilities

Generic key-value storage manager for .bnbagent_state file.
"""

import os
import json
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any

from .logger import get_logger


class StateFileManager:
    """
    Generic key-value storage manager for .bnbagent_state file.
    """

    def __init__(self, state_file_path: Optional[Path] = None, debug: bool = False):
        """
        Initialize the state file manager.

        Args:
            state_file_path: Optional path to state file.
                            Defaults to .bnbagent_state in current directory.
            debug: Enable debug logging
        """
        self.debug = debug
        self._logger = get_logger(f"{__name__}.{self.__class__.__name__}", debug=debug)

        if state_file_path is None:
            self.state_file_path = Path.cwd() / ".bnbagent_state"
        else:
            self.state_file_path = Path(state_file_path)

    def load(self) -> Dict[str, Any]:
        """
        Load all state from file.

        Returns:
            dict: All state data

        Raises:
            FileNotFoundError: If state file does not exist
            ValueError: If state file format is invalid
        """
        if not self.state_file_path.exists():
            raise FileNotFoundError(f"State file not found: {self.state_file_path}")

        try:
            with open(self.state_file_path, "r") as f:
                data = json.load(f)
                return data
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid state file format: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Failed to load state file: {str(e)}")

    def save(self, data: Dict[str, Any]) -> None:
        """
        Save state to file atomically (overwrites entire file).

        Uses atomic write (write to temp file, then rename) to prevent
        data corruption if the process crashes during write.

        Args:
            data: State data dictionary
        """
        try:
            # Ensure directory exists
            self.state_file_path.parent.mkdir(parents=True, exist_ok=True)

            # Atomic write: write to temp file first, then rename
            # This prevents corruption if process crashes during write
            fd, temp_path = tempfile.mkstemp(
                dir=self.state_file_path.parent,
                prefix=".state_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f)

                # Set file permissions before rename (0o600 = owner read/write only)
                os.chmod(temp_path, 0o600)

                # Atomic rename (POSIX guarantees atomicity)
                os.replace(temp_path, self.state_file_path)

            except Exception:
                # Clean up temp file on error
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise

            self._logger.debug(f"Saved state to file: {self.state_file_path}")

        except Exception as e:
            raise RuntimeError(f"Failed to save state file: {str(e)}")

    def get(self, key: str, default: Optional[Any] = None) -> Optional[Any]:
        """
        Get a value by key from state file.

        Args:
            key: The key to retrieve
            default: Default value to return if key not found or file doesn't exist

        Returns:
            Optional[Any]: The value for the key, or default if not found
        """
        try:
            data = self.load()
            return data.get(key, default)
        except (FileNotFoundError, ValueError, RuntimeError):
            return default

    def set(self, key: str, value: Any) -> None:
        """
        Set a value by key in state file.

        Args:
            key: The key to set
            value: The value to store
        """
        # Load existing data or create new
        try:
            data = self.load()
        except FileNotFoundError:
            data = {}

        data[key] = value
        self.save(data)

    def exists(self) -> bool:
        """
        Check if state file exists.

        Returns:
            bool: True if state file exists
        """
        return self.state_file_path.exists()
