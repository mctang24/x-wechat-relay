from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("X_MODEL_ALERT_DATA_DIR", PROJECT_ROOT / "data"))
LOG_DIR = PROJECT_ROOT / "logs"
WECHAT_CRED_PATH = DATA_DIR / "wechatbot" / "credentials.json"
WECHAT_BINDING_PATH = DATA_DIR / "wechat_binding.json"
X_COOKIES_PATH = DATA_DIR / "x_cookies.json"
X_HANDLES_PATH = DATA_DIR / "x_handles.txt"
X_STATE_PATH = DATA_DIR / "x_state.json"
