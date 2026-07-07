from __future__ import annotations

import argparse
import asyncio
import socket
from datetime import datetime
from html import escape
from pathlib import Path

from wechatbot import WeChatBot

from .paths import DATA_DIR, WECHAT_BINDING_PATH, WECHAT_CRED_PATH
from .runtime_log import error_log
from .wechat_state import load_binding, same_user, save_binding

BIND_COMMAND = "bind"
TEST_MESSAGE = "X WeChat Relay test message."
ALERT_PREFIX = "X WeChat Relay warning"
WECHATBOT_HOST = "ilinkai.weixin.qq.com"
WECHATBOT_FALLBACK_IPS = ("220.196.154.96", "182.50.15.221", "182.50.15.252", "140.207.56.23")
_DNS_FALLBACK_INSTALLED = False


def install_wechatbot_dns_fallback() -> None:
    global _DNS_FALLBACK_INSTALLED
    if _DNS_FALLBACK_INSTALLED:
        return
    original = socket.getaddrinfo

    def fallback_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        try:
            return original(host, port, family, type, proto, flags)
        except socket.gaierror:
            if host != WECHATBOT_HOST:
                raise
            results = []
            for ip in WECHATBOT_FALLBACK_IPS:
                results.extend(original(ip, port, family, type, proto, flags))
            return results

    socket.getaddrinfo = fallback_getaddrinfo
    _DNS_FALLBACK_INSTALLED = True



def format_warning(reason: str) -> str:
    clean_reason = " ".join(reason.strip().split())
    if not clean_reason:
        clean_reason = "Unknown warning"
    return f"{ALERT_PREFIX}: {clean_reason}"



def write_qr_page(url: str) -> Path:
    qr_path = DATA_DIR / "wechat_login_qr.html"
    qr_path.parent.mkdir(parents=True, exist_ok=True)
    safe_url = escape(url, quote=True)
    generated_at = escape(datetime.now().strftime('%H:%M:%S'))
    qr_path.write_text(
        "<!doctype html>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>WeChat Login</title>"
        "<body style=\"font-family:sans-serif;text-align:center;margin:0;padding:24px;background:#fff\">"
        "<h1>WeChat Login</h1>"
        f"<p>Generated at {generated_at}</p>"
        f"<p><a href=\"{safe_url}\" target=\"_blank\" rel=\"noreferrer\">Open login page</a></p>"
        f"<iframe src=\"{safe_url}\" title=\"WeChat login\" style=\"width:420px;height:520px;max-width:96vw;border:1px solid #ddd\"></iframe>"
        "<p>Scan the QR code shown above with WeChat.</p>"
        "</body>",
        encoding="utf-8",
    )
    return qr_path


def build_bot() -> WeChatBot:
    WECHAT_CRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    return WeChatBot(
        cred_path=str(WECHAT_CRED_PATH),
        on_qr_url=lambda url: print(f"[{datetime.now().strftime('%H:%M:%S')}] 已生成本地扫码页面: {write_qr_page(url)}"),
        on_scanned=lambda: print("已扫码，等待确认登录。"),
        on_expired=lambda: print("二维码已过期，等待新的登录链接。"),
        on_error=lambda err: error_log(f"微信 bot 错误: {type(err).__name__}"),
    )


def short_status(path: Path = WECHAT_BINDING_PATH) -> str:
    binding = load_binding(path)
    if binding is None:
        return "未绑定"
    return f"已绑定，绑定时间: {binding.bound_at}"


def bot_identity_ids(bot: WeChatBot) -> set[str]:
    ids = set()
    credentials = getattr(bot, "_credentials", None)
    if credentials is not None:
        value = str(getattr(credentials, "account_id", "") or "")
        if value:
            ids.add(value)
    value = str(getattr(bot, "account_id", "") or "")
    if value:
        ids.add(value)
    return ids


def resolve_send_user_id(bot: WeChatBot, msg) -> str:
    return str(msg.user_id)


def is_bot_self(bot: WeChatBot, user_id: str) -> bool:
    return user_id in bot_identity_ids(bot)


def refresh_binding_context(user_id: str, context_token: str, path: Path = WECHAT_BINDING_PATH) -> bool:
    binding = load_binding(path)
    if not same_user(binding, user_id):
        return False
    save_binding(path, binding.user_id, context_token)
    return True


def run_bot() -> None:
    try:
        asyncio.run(run_bot_async())
    except Exception as exc:
        error_log(f"微信 bot 运行失败: {type(exc).__name__}")
        raise


async def run_bot_async() -> None:
    install_wechatbot_dns_fallback()
    bot = build_bot()

    @bot.on_message
    async def handle(msg):
        text = (msg.text or "").strip().lower()
        if refresh_binding_context(msg.user_id, msg._context_token):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 已刷新微信发送上下文。", flush=True)

        if text == BIND_COMMAND:
            send_user_id = resolve_send_user_id(bot, msg)
            if is_bot_self(bot, send_user_id):
                await bot.reply(msg, "绑定失败。")
                return

            binding = load_binding(WECHAT_BINDING_PATH)
            if binding is None:
                save_binding(WECHAT_BINDING_PATH, send_user_id, msg._context_token)
                await bot.reply(msg, "已绑定。")
            elif same_user(binding, send_user_id):
                save_binding(WECHAT_BINDING_PATH, send_user_id, msg._context_token)
                await bot.reply(msg, "已绑定。")
            else:
                await bot.reply(msg, "已绑定其他帐号。")
            return

        if text == "test":
            binding = load_binding(WECHAT_BINDING_PATH)
            if binding is None:
                await bot.reply(msg, "未绑定，请先发送 bind。")
                return
            if not same_user(binding, msg.user_id):
                await bot.reply(msg, "当前帐号未绑定。")
                return
            save_binding(WECHAT_BINDING_PATH, binding.user_id, msg._context_token)
            await bot.send(binding.user_id, TEST_MESSAGE)
            return

    await bot.login()
    monitor_task = asyncio.create_task(start_monitor(bot))
    try:
        await bot.start()
    finally:
        monitor_task.cancel()
        manual_task.cancel()


async def start_monitor(bot: WeChatBot) -> None:
    from .monitor import monitor_loop

    await monitor_loop(bot)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="x-wechat-relay-wechat")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run")
    subparsers.add_parser("status")
    args = parser.parse_args(argv)

    if args.command == "run":
        run_bot()
    elif args.command == "status":
        print(short_status())


if __name__ == "__main__":
    main()
