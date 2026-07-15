from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable

from twikit import Client

from .paths import X_COOKIES_PATH, X_HANDLES_PATH

DEFAULT_HANDLES = ("OpenAI", "AnthropicAI", "claudeai")
X_HANDLES_ENV = "X_MODEL_ALERT_HANDLES"
DEFAULT_TWEET_COUNT = 3
X_REQUEST_TIMEOUT_SECONDS = 30.0
X_STATIC_ASSET_HOST = "abs.twimg.com"
X_STATIC_ASSET_FALLBACK_IPS = ("104.18.39.59", "172.64.148.197")
_X_DNS_FALLBACK_INSTALLED = False


def x_fallback_getaddrinfo(original, host, port, family=0, type=0, proto=0, flags=0):
    normalized_host = host.decode("ascii") if isinstance(host, bytes) else host
    if normalized_host != X_STATIC_ASSET_HOST:
        return original(host, port, family, type, proto, flags)
    results = []
    for ip in X_STATIC_ASSET_FALLBACK_IPS:
        results.extend(original(ip, port, family, type, proto, flags))
    return results


def install_x_dns_fallback() -> None:
    global _X_DNS_FALLBACK_INSTALLED
    if _X_DNS_FALLBACK_INSTALLED:
        return
    original = socket.getaddrinfo

    def fallback_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return x_fallback_getaddrinfo(original, host, port, family, type, proto, flags)

    socket.getaddrinfo = fallback_getaddrinfo
    _X_DNS_FALLBACK_INSTALLED = True


def parse_handles(raw: str) -> tuple[str, ...]:
    handles: list[str] = []
    seen: set[str] = set()
    for item in raw.replace("\n", ",").split(","):
        handle = item.strip().lstrip("@").strip()
        if not handle or handle in seen:
            continue
        seen.add(handle)
        handles.append(handle)
    return tuple(handles)


def save_handles_file(raw: str, path: Path = X_HANDLES_PATH) -> Path:
    handles = parse_handles(raw)
    if not handles:
        raise SystemExit("未配置有效 X 账号")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(handles) + "\n", encoding="utf-8")
    return path


def configured_handles(environ: dict[str, str] | None = None, path: Path = X_HANDLES_PATH) -> tuple[str, ...]:
    env = os.environ if environ is None else environ
    raw = env.get(X_HANDLES_ENV, "")
    if raw.strip():
        handles = parse_handles(raw)
        if not handles:
            raise SystemExit(f"{X_HANDLES_ENV} 未配置有效 X 账号")
        return handles
    if path.exists():
        handles = parse_handles(path.read_text(encoding="utf-8"))
        if handles:
            return handles
    return DEFAULT_HANDLES


@dataclass(frozen=True)
class Tweet:
    account: str
    id: str
    text: str
    url: str
    created_at: str


def init_cookies_file(path: Path = X_COOKIES_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps({}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def ensure_cookies_file(path: Path = X_COOKIES_PATH) -> Path:
    if not path.exists():
        raise SystemExit(f"缺少 X cookie 文件，请先准备: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise SystemExit(f"X cookie 文件为空，请填入 twifork/twikit cookie JSON: {path}")
    return path


def format_tweet_message(tweet: Tweet) -> str:
    text = " ".join(tweet.text.strip().split())
    return "\n".join((tweet.account, text, tweet.url))


def format_digest_message(tweets: Iterable[Tweet]) -> str:
    lines = ["X updates"]
    for tweet in tweets:
        text = " ".join(tweet.text.strip().split())
        lines.extend(("", tweet.account, text, tweet.url))
    return "\n".join(lines)


def tweet_sort_key(tweet: Tweet) -> datetime:
    try:
        parsed = parsedate_to_datetime(tweet.created_at)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(tweet.created_at.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_tweets(account: str, tweets: Iterable[object]) -> list[Tweet]:
    normalized: list[Tweet] = []
    for tweet in tweets:
        tweet_id = str(getattr(tweet, "id", "") or "").strip()
        text = str(getattr(tweet, "full_text", None) or getattr(tweet, "text", "") or "").strip()
        created_at = str(getattr(tweet, "created_at", "") or "").strip()
        if not tweet_id or not text or not created_at:
            continue
        normalized.append(
            Tweet(
                account=account,
                id=tweet_id,
                text=text,
                url=f"https://x.com/{account}/status/{tweet_id}",
                created_at=created_at,
            )
        )
    return sorted(normalized, key=tweet_sort_key, reverse=True)


async def fetch_latest_tweets(
    handles: Iterable[str] | None = None,
    *,
    cookies_path: Path = X_COOKIES_PATH,
    count: int = DEFAULT_TWEET_COUNT,
) -> list[Tweet]:
    install_x_dns_fallback()
    handles = configured_handles() if handles is None else tuple(handles)
    cookies_path = ensure_cookies_file(cookies_path)
    client = Client("en-US", timeout=X_REQUEST_TIMEOUT_SECONDS)
    client.load_cookies(str(cookies_path))

    tweets: list[Tweet] = []
    for handle in handles:
        account = handle.lstrip("@")
        user = await client.get_user_by_screen_name(account)
        result = await user.get_tweets("Tweets", count=count)
        tweets.extend(normalize_tweets(account, list(result)[:count]))
    return tweets


def summarize_tweets(tweets: Iterable[Tweet]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for tweet in tweets:
        summary[tweet.account] = summary.get(tweet.account, 0) + 1
    return summary


async def run_live_check() -> None:
    handles = configured_handles()
    tweets = await fetch_latest_tweets(handles)
    summary = summarize_tweets(tweets)
    for handle in handles:
        print(f"{handle}: {summary.get(handle, 0)} tweets")
    print(f"Fetched {len(tweets)} tweets from twifork.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="x-wechat-relay-x-source")
    parser.add_argument("--init-cookies", action="store_true")
    parser.add_argument("--set-handles")
    args = parser.parse_args(argv)

    if args.set_handles:
        path = save_handles_file(args.set_handles)
        print(f"X handles file: {path}")

    if args.init_cookies:
        path = init_cookies_file()
        print(f"X cookies file: {path}")
        return

    asyncio.run(run_live_check())


if __name__ == "__main__":
    main()
