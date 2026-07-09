from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone

from wechatbot import WeChatBot

from .runtime_log import error_log
from .scheduler import next_cron_time, schedule_now
from .wechat_bot import format_warning, send_text_to_binding
from .paths import LOG_DIR, MONITOR_STATUS_PATH, WECHAT_BINDING_PATH
from .wechat_state import load_binding
from .x_source import fetch_latest_tweets, format_digest_message
from .x_state import load_last_ids, plan_new_tweets, save_last_ids

X_CHECK_TIMEOUT_SECONDS = 120.0


def write_monitor_status(event: str, **fields: object) -> None:
    MONITOR_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "event": event,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    MONITOR_STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "monitor.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def sleep_with_heartbeat_sync(total_seconds: int, *, event: str, stop_event: threading.Event, **fields: object) -> bool:
    remaining = max(0, total_seconds)
    while remaining > 0 and not stop_event.is_set():
        step = min(60, remaining)
        write_monitor_status(event, remaining_seconds=remaining, **fields)
        if stop_event.wait(step):
            return False
        remaining -= step
    return not stop_event.is_set()


def sleep_until_sync(target_time: datetime, *, event: str, stop_event: threading.Event, **fields: object) -> bool:
    while not stop_event.is_set():
        now = schedule_now()
        remaining = max(0, int((target_time - now).total_seconds()))
        if remaining <= 0:
            return True
        step = min(30, remaining)
        write_monitor_status(event, schedule_now=now.isoformat(), remaining_seconds=remaining, **fields)
        if stop_event.wait(step):
            return False
    return False


def send_to_binding_from_thread(bot: WeChatBot, loop: asyncio.AbstractEventLoop, text: str) -> bool:
    future = asyncio.run_coroutine_threadsafe(send_text_to_binding(bot, text), loop)
    return bool(future.result(timeout=60))


def fetch_latest_tweets_with_timeout() -> list:
    return asyncio.run(asyncio.wait_for(fetch_latest_tweets(), timeout=X_CHECK_TIMEOUT_SECONDS))


def run_check_once_from_thread(bot: WeChatBot, loop: asyncio.AbstractEventLoop) -> int:
    started = time.monotonic()
    write_monitor_status("fetching_x")
    tweets = fetch_latest_tweets_with_timeout()
    write_monitor_status("fetched_x", tweet_count=len(tweets), elapsed_seconds=round(time.monotonic() - started, 1))
    last_ids = load_last_ids()
    first_run = not last_ids
    candidates, next_last_ids = plan_new_tweets(tweets, last_ids)

    if first_run:
        save_last_ids(next_last_ids)
        print("X 首次运行已建立基线，不推送历史内容。", flush=True)
        return 0

    sent_count = 0
    if candidates:
        try:
            write_monitor_status("sending_wechat", tweet_count=len(candidates), message_count=1)
            if send_to_binding_from_thread(bot, loop, format_digest_message(candidates)):
                sent_count = len(candidates)
        except (FutureTimeoutError, Exception) as exc:
            error_log(f"微信推送失败，本轮摘要未送达: {type(exc).__name__}")

    save_last_ids(next_last_ids)
    if candidates and sent_count == 0:
        error_log(f"微信推送失败，但本轮已按 cron 规则推进 X 状态: 0/{len(candidates)}")
    print(f"X 检查完成，发现 {len(candidates)} 条，摘要推送 {sent_count} 条。", flush=True)
    write_monitor_status("checked", sent_count=sent_count, candidate_count=len(candidates), elapsed_seconds=round(time.monotonic() - started, 1))
    return sent_count


def monitor_thread_loop(bot: WeChatBot, loop: asyncio.AbstractEventLoop, stop_event: threading.Event) -> None:
    write_monitor_status("started")
    target_time = next_cron_time(schedule_now())
    while not stop_event.is_set():
        write_monitor_status("waiting_cron", scheduled_at=target_time.isoformat())
        if not sleep_until_sync(target_time, event="waiting_cron", stop_event=stop_event, scheduled_at=target_time.isoformat()):
            break

        scheduled_at = target_time.isoformat()
        target_time = next_cron_time(target_time)
        try:
            started = time.monotonic()
            write_monitor_status("checking", scheduled_at=scheduled_at)
            sent_count = run_check_once_from_thread(bot, loop)
            write_monitor_status("checked", sent_count=sent_count, scheduled_at=scheduled_at, elapsed_seconds=round(time.monotonic() - started, 1))
        except Exception as exc:
            write_monitor_status("check_failed", error=type(exc).__name__, scheduled_at=scheduled_at)
            error_log(f"X 检查失败: {type(exc).__name__}")
            try:
                send_to_binding_from_thread(bot, loop, format_warning(f"X check failed: {type(exc).__name__}"))
            except Exception as send_exc:
                error_log(f"微信告警发送失败: {type(send_exc).__name__}")


def start_monitor_thread(bot: WeChatBot, loop: asyncio.AbstractEventLoop) -> tuple[threading.Thread, threading.Event]:
    stop_event = threading.Event()
    thread = threading.Thread(target=monitor_thread_loop, args=(bot, loop, stop_event), name="x-wechat-relay-monitor", daemon=True)
    thread.start()
    return thread, stop_event
