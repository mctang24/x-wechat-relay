from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class WechatBinding:
    user_id: str
    context_token: str
    bound_at: str


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_binding(path: Path) -> WechatBinding | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return WechatBinding(user_id=data["user_id"], context_token=data.get("context_token", ""), bound_at=data["bound_at"])


def save_binding(path: Path, user_id: str, context_token: str = "") -> WechatBinding:
    path.parent.mkdir(parents=True, exist_ok=True)
    binding = WechatBinding(user_id=user_id, context_token=context_token, bound_at=now_iso())
    path.write_text(json.dumps(asdict(binding), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return binding


def same_user(binding: WechatBinding | None, user_id: str) -> bool:
    return binding is not None and binding.user_id == user_id
