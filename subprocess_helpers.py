from __future__ import annotations

import os
import subprocess
from typing import Any


def run_without_console(*popenargs: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    if os.name == "nt":
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if create_no_window:
            kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | create_no_window
    return subprocess.run(*popenargs, **kwargs)
