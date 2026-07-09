from __future__ import annotations

import sys
from datetime import datetime

from .paths import LOG_DIR


def error_log(message: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    print(message, file=sys.stderr, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "wechat.err.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
