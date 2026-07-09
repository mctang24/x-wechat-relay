from __future__ import annotations

import argparse
import asyncio
import json
import socket
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from wechatbot import WeChatBot

from .paths import DATA_DIR, LOG_DIR, WECHAT_BINDING_PATH, WECHAT_CRED_PATH, WECHAT_STATUS_PATH
from .runtime_log import error_log
from .wechat_state import WechatBinding, load_binding, same_user, save_binding

BIND_COMMAND = "bind"
TEST_COMMAND = "test"
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


def write_wechat_status(event: str, **fields: object) -> None:
    WECHAT_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"event": event, "updated_at": datetime.now(timezone.utc).isoformat(), **fields}
    WECHAT_STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "wechat_status.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def context_age_seconds(binding: WechatBinding) -> int | None:
    try:
        updated_at = datetime.fromisoformat(binding.context_updated_at)
    except ValueError:
        return None
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)).total_seconds()))


def wechat_error_fields(exc: Exception) -> dict[str, object]:
    fields: dict[str, object] = {"error": type(exc).__name__}
    for attr in ("http_status", "errcode"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            fields[attr] = value
    is_session_expired = getattr(exc, "is_session_expired", None)
    if isinstance(is_session_expired, bool):
        fields["session_expired"] = is_session_expired
    payload = getattr(exc, "payload", None)
    if isinstance(payload, dict):
        for key in ("ret", "errcode", "errmsg"):
            value = payload.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                fields[f"payload_{key}"] = value
    return fields


def write_qr_page(url: str) -> Path:
    qr_path = DATA_DIR / "wechat_login_qr.html"
    qr_path.parent.mkdir(parents=True, exist_ok=True)
    safe_url = escape(url, quote=True)
    generated_at = escape(datetime.now().strftime("%H:%M:%S"))
    html = (
        '<!doctype html>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>WeChat Login</title>'
        '<body style="font-family:sans-serif;text-align:center;margin:0;padding:24px;background:#fff">'
        '<h1>WeChat Login</h1>'
        f'<p>Generated at {generated_at}</p>'
        f'<p><a href="{safe_url}" target="_blank" rel="noreferrer">Open login page</a></p>'
        f'<iframe src="{safe_url}" title="WeChat login" style="width:420px;height:520px;max-width:96vw;border:1px solid #ddd"></iframe>'
        '<p>Scan the QR code shown above with WeChat.</p>'
        '</body>'
    )
    qr_path.write_text(html, encoding="utf-8")
    return qr_path

def handle_bot_error(err: Exception) -> None:
    name = type(err).__name__
    write_wechat_status("longpoll_timeout" if name == "TimeoutError" else "bot_error", error=name)
    if name != "TimeoutError":
        error_log(f"微信 bot 错误: {name}")


def build_bot() -> WeChatBot:
    WECHAT_CRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    return WeChatBot(
        cred_path=str(WECHAT_CRED_PATH),
        on_qr_url=lambda url: print(f"[{datetime.now().strftime('%H:%M:%S')}] 已生成本地扫码页面: {write_qr_page(url)}"),
        on_scanned=lambda: print("已扫码，等待确认登录。"),
        on_expired=lambda: print("二维码已过期，等待新的登录链接。"),
        on_error=handle_bot_error,
    )


def format_warning(reason: str) -> str:
    clean_reason = " ".join(reason.strip().split()) or "Unknown warning"
    return f"{ALERT_PREFIX}: {clean_reason}"


def bot_identity_ids(bot: WeChatBot) -> set[str]:
    ids = set()
    credentials = getattr(bot, "_credentials", None)
    if credentials is not None:
        account_id = str(getattr(credentials, "account_id", "") or "")
        if account_id:
            ids.add(account_id)
    account_id = str(getattr(bot, "account_id", "") or "")
    if account_id:
        ids.add(account_id)
    return ids


def is_bot_self(bot: WeChatBot, user_id: str) -> bool:
    return user_id in bot_identity_ids(bot)


def remember_context(bot: WeChatBot, user_id: str, context_token: str) -> bool:
    if not user_id or not context_token:
        return False
    tokens = getattr(bot, "_context_tokens", None)
    if not isinstance(tokens, dict):
        return False
    tokens[user_id] = context_token
    return True


def hydrate_saved_context(bot: WeChatBot, path: Path = WECHAT_BINDING_PATH) -> bool:
    binding = load_binding(path)
    if binding is None or not binding.context_token or is_bot_self(bot, binding.user_id):
        return False
    ok = remember_context(bot, binding.user_id, binding.context_token)
    write_wechat_status("context_hydrated", hydrated=ok)
    return ok


def resolve_send_user_id(bot: WeChatBot, msg) -> str:
    return str(msg.user_id)


def refresh_binding_context(user_id: str, context_token: str, path: Path = WECHAT_BINDING_PATH) -> bool:
    binding = load_binding(path)
    if not same_user(binding, user_id):
        return False
    save_binding(path, binding.user_id, context_token, bound_at=binding.bound_at)
    return True


async def send_text_to_binding(bot: WeChatBot, text: str, path: Path | None = None) -> bool:
    path = WECHAT_BINDING_PATH if path is None else path
    binding = load_binding(path)
    if binding is None:
        write_wechat_status("send_skipped", reason="no_binding")
        return False
    if is_bot_self(bot, binding.user_id):
        write_wechat_status("send_skipped", reason="self_binding")
        return False
    if binding.context_token:
        remember_context(bot, binding.user_id, binding.context_token)
    try:
        await bot.send(binding.user_id, text)
    except Exception as exc:
        write_wechat_status(
            "send_failed",
            context_updated_at=binding.context_updated_at,
            context_age_seconds=context_age_seconds(binding),
            **wechat_error_fields(exc),
        )
        raise
    write_wechat_status(
        "send_succeeded",
        context_updated_at=binding.context_updated_at,
        context_age_seconds=context_age_seconds(binding),
    )
    return True


def short_status(path: Path = WECHAT_BINDING_PATH) -> str:
    binding = load_binding(path)
    if binding is None:
        return "未绑定"
    return f"已绑定，绑定时间: {binding.bound_at}"


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
        remember_context(bot, msg.user_id, msg._context_token)
        write_wechat_status("inbound_message")

        if refresh_binding_context(msg.user_id, msg._context_token):
            write_wechat_status("context_refreshed")

        if text == BIND_COMMAND:
            send_user_id = resolve_send_user_id(bot, msg)
            if is_bot_self(bot, send_user_id):
                await bot.reply(msg, "绑定失败。")
                return
            binding = load_binding(WECHAT_BINDING_PATH)
            if binding is None or same_user(binding, send_user_id):
                save_binding(WECHAT_BINDING_PATH, send_user_id, msg._context_token)
                remember_context(bot, send_user_id, msg._context_token)
                write_wechat_status("bound")
                await bot.reply(msg, "已绑定。")
                return
            await bot.reply(msg, "已绑定其他帐号。")
            return

        if text == TEST_COMMAND:
            binding = load_binding(WECHAT_BINDING_PATH)
            if binding is None:
                await bot.reply(msg, "未绑定，请先发送 bind。")
                return
            if not same_user(binding, msg.user_id):
                await bot.reply(msg, "当前帐号未绑定。")
                return
            save_binding(WECHAT_BINDING_PATH, binding.user_id, msg._context_token)
            remember_context(bot, binding.user_id, msg._context_token)
            write_wechat_status("test_requested")
            await send_text_to_binding(bot, TEST_MESSAGE)
            write_wechat_status("test_sent")

    await bot.login()
    context_hydrated = hydrate_saved_context(bot)
    write_wechat_status("logged_in", context_hydrated=context_hydrated)

    from .monitor import start_monitor_thread

    loop = asyncio.get_running_loop()
    monitor_thread, monitor_stop = start_monitor_thread(bot, loop)
    try:
        await bot.start()
    finally:
        monitor_stop.set()
        monitor_thread.join(timeout=5)


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
