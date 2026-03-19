from __future__ import annotations

import warnings

warnings.warn(
    "Importing from bnbagent.core.exceptions is deprecated. Use bnbagent.exceptions instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ..exceptions import *  # noqa: F401, F403, E402
