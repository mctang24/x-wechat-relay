from __future__ import annotations

import json
from pathlib import Path

from .paths import X_STATE_PATH
from .x_source import Tweet


def load_last_ids(path: Path = X_STATE_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(account): str(tweet_id) for account, tweet_id in data.items()}


def save_last_ids(last_ids: dict[str, str], path: Path = X_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(last_ids, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def plan_new_tweets(tweets: list[Tweet], last_ids: dict[str, str]) -> tuple[list[Tweet], dict[str, str]]:
    next_ids = dict(last_ids)
    candidates: list[Tweet] = []

    seen_accounts: set[str] = set()
    stopped_accounts: set[str] = set()
    for tweet in tweets:
        if tweet.account in stopped_accounts:
            continue
        if tweet.account not in seen_accounts:
            next_ids[tweet.account] = tweet.id
            seen_accounts.add(tweet.account)
        if last_ids.get(tweet.account) == tweet.id:
            stopped_accounts.add(tweet.account)
            continue
        candidates.append(tweet)

    return candidates, next_ids
