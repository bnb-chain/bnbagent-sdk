from pathlib import Path
from unittest.mock import MagicMock, patch
import importlib.util
import pytest


def test_unsupported_cli_does_not_retry_password_on_argv(tmp_path):
    path = Path(__file__).parents[1] / "examples/twak/quickstart.py"
    spec = importlib.util.spec_from_file_location("safe_twak_quickstart", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = MagicMock()
    provider.create_wallet.side_effect = RuntimeError("CLI requires unsupported password argument")
    with patch("subprocess.run") as run:
        with pytest.raises(RuntimeError, match="unsupported"):
            module.create_throwaway_wallet(provider, tmp_path)
    run.assert_not_called()
