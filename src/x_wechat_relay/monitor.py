from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from wechatbot import WeChatBot

from .runtime_log import error_log
from .scheduler import eastern_now, random_jitter_seconds, seconds_until_next_check
from .wechat_bot import is_bot_self, format_warning
from .paths import WECHAT_BINDING_PATH
from .wechat_state import load_binding
from .x_source import fetch_latest_tweets, format_tweet_message
from .x_state import load_last_ids, plan_new_tweets, save_last_ids

Sleep = Callable[[float], Awaitable[None]]


async def send_to_binding(bot: WeChatBot, text: str) -> bool:
    binding = load_binding(WECHAT_BINDING_PATH)
    if binding is None:
        print("未绑定微信接收人，跳过发送。", flush=True)
        return False
    if is_bot_self(bot, binding.user_id):
        print("微信绑定目标是 bot 自己，跳过发送。", flush=True)
        return False
    await bot.send(binding.user_id, text)
    return True


async def run_check_once(bot: WeChatBot) -> int:
    tweets = await fetch_latest_tweets()
    last_ids = load_last_ids()
    first_run = not last_ids
    candidates, next_last_ids = plan_new_tweets(tweets, last_ids)

    if first_run:
        save_last_ids(next_last_ids)
        print("X 首次运行已建立基线，不推送历史内容。", flush=True)
        return 0

    sent_count = 0
    for tweet in candidates:
        try:
            if await send_to_binding(bot, format_tweet_message(tweet)):
                sent_count += 1
        except Exception as exc:
            error_log(f"微信推送失败，跳过本条: {type(exc).__name__}")
        await asyncio.sleep(10)

    save_last_ids(next_last_ids)
    print(f"X 检查完成，发现 {len(candidates)} 条，推送 {sent_count} 条。", flush=True)
    return sent_count


async def monitor_loop(bot: WeChatBot, sleep: Sleep = asyncio.sleep) -> None:
    while True:
        delay = seconds_until_next_check(eastern_now())
        if delay:
            print(f"距离下一次美东工作日基准检查点还有 {delay} 秒。", flush=True)
            await sleep(delay)

        jitter = random_jitter_seconds()
        if jitter:
            print(f"本轮随机等待 {jitter} 秒后检查。", flush=True)
            await sleep(jitter)

        try:
            await run_check_once(bot)
        except Exception as exc:
            error_log(f"X 检查失败: {type(exc).__name__}")
            try:
                await send_to_binding(bot, format_warning(f"X check failed: {type(exc).__name__}"))
            except Exception as send_exc:
                error_log(f"微信告警发送失败: {type(send_exc).__name__}")

        await sleep(max(1, seconds_until_next_check(eastern_now())))
