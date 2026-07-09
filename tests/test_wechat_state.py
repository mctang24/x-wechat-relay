from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from x_wechat_relay.monitor import run_check_once_from_thread
from x_wechat_relay.scheduler import CRON_EXPRESSION, next_cron_time, seconds_until_next_check
from x_wechat_relay.wechat_bot import bot_identity_ids, format_warning, hydrate_saved_context, is_bot_self, refresh_binding_context, remember_context, resolve_send_user_id, wechat_error_fields
from x_wechat_relay.wechat_state import load_binding, same_user, save_binding


class WechatStateTest(unittest.TestCase):
    def test_save_and_load_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "binding.json"
            saved = save_binding(path, "user-1", "ctx-1")
            loaded = load_binding(path)

            self.assertEqual(saved, loaded)
            self.assertTrue(same_user(loaded, "user-1"))
            self.assertFalse(same_user(loaded, "user-2"))

    def test_saved_json_is_minimal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "binding.json"
            save_binding(path, "user-1", "ctx-1")
            data = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(set(data), {"user_id", "context_token", "bound_at", "context_updated_at"})

    def test_refresh_binding_context_updates_bound_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "binding.json"
            save_binding(path, "user-1", "old")
            old_bound_at = load_binding(path).bound_at

            self.assertTrue(refresh_binding_context("user-1", "new", path))
            loaded = load_binding(path)
            self.assertEqual(loaded.context_token, "new")
            self.assertEqual(loaded.bound_at, old_bound_at)
            self.assertGreaterEqual(loaded.context_updated_at, old_bound_at)

    def test_refresh_binding_context_ignores_other_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "binding.json"
            save_binding(path, "user-1", "old")

            self.assertFalse(refresh_binding_context("user-2", "new", path))
            self.assertEqual(load_binding(path).context_token, "old")

    def test_hydrate_saved_context_restores_sdk_memory(self) -> None:
        import x_wechat_relay.wechat_bot as wechat_bot

        class Credentials:
            account_id = "bot-1"

        class Bot:
            _credentials = Credentials()
            _context_tokens = {}

        old_status_path = wechat_bot.WECHAT_STATUS_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "binding.json"
            status_path = Path(tmpdir) / "wechat_status.json"
            save_binding(path, "user-1", "ctx-1")
            wechat_bot.WECHAT_STATUS_PATH = status_path

            try:
                self.assertTrue(hydrate_saved_context(Bot(), path))
                self.assertEqual(Bot._context_tokens, {"user-1": "ctx-1"})
            finally:
                wechat_bot.WECHAT_STATUS_PATH = old_status_path

    def test_remember_context_updates_sdk_memory(self) -> None:
        class Bot:
            _context_tokens = {}

        self.assertTrue(remember_context(Bot(), "user-1", "ctx-1"))
        self.assertEqual(Bot._context_tokens, {"user-1": "ctx-1"})

    def test_wechat_error_fields_include_api_diagnostics(self) -> None:
        class ApiLikeError(Exception):
            http_status = 200
            errcode = -14
            is_session_expired = True
            payload = {"ret": 0, "errcode": -14, "errmsg": "expired", "token": "secret"}

        fields = wechat_error_fields(ApiLikeError("failed"))

        self.assertEqual(fields["error"], "ApiLikeError")
        self.assertEqual(fields["http_status"], 200)
        self.assertEqual(fields["errcode"], -14)
        self.assertTrue(fields["session_expired"])
        self.assertEqual(fields["payload_errmsg"], "expired")
        self.assertNotIn("payload_token", fields)


class WechatWarningTest(unittest.TestCase):
    def test_format_warning_compacts_reason(self) -> None:
        self.assertEqual(format_warning("  login   expired  "), "X WeChat Relay warning: login expired")

    def test_format_warning_handles_empty_reason(self) -> None:
        self.assertEqual(format_warning("   "), "X WeChat Relay warning: Unknown warning")

    def test_format_warning_compacts_newlines(self) -> None:
        self.assertEqual(format_warning("a\nb"), "X WeChat Relay warning: a b")

    def test_is_bot_self_uses_bot_identity_ids(self) -> None:
        class Credentials:
            account_id = "bot-1"
            user_id = "login-user"

        class Bot:
            _credentials = Credentials()

        self.assertEqual(bot_identity_ids(Bot()), {"bot-1"})
        self.assertTrue(is_bot_self(Bot(), "bot-1"))
        self.assertFalse(is_bot_self(Bot(), "login-user"))
        self.assertFalse(is_bot_self(Bot(), "user-1"))

    def test_resolve_send_user_id_uses_incoming_sender(self) -> None:
        class Bot:
            pass

        class Msg:
            user_id = "sender"
            _context_token = "ctx-1"
            raw = {"to_user_id": "target-2"}

        self.assertEqual(resolve_send_user_id(Bot(), Msg()), "sender")

    def test_resolve_send_user_id_falls_back_to_raw_target(self) -> None:
        class Bot:
            pass

        class Msg:
            user_id = "sender"
            _context_token = "ctx-1"
            raw = {"to_user_id": "target-2"}

        self.assertEqual(resolve_send_user_id(Bot(), Msg()), "sender")

    def test_scheduler_uses_cron_expression(self) -> None:
        self.assertEqual(CRON_EXPRESSION, "0 9-17/2 * * *")

    def test_scheduler_next_cron_time(self) -> None:
        eastern = ZoneInfo("America/New_York")
        self.assertEqual(next_cron_time(datetime(2026, 7, 8, 8, 59, tzinfo=eastern)).hour, 9)
        self.assertEqual(next_cron_time(datetime(2026, 7, 8, 9, 1, tzinfo=eastern)).hour, 11)
        self.assertEqual(next_cron_time(datetime(2026, 7, 8, 11, 30, tzinfo=eastern)).hour, 13)
        self.assertEqual(next_cron_time(datetime(2026, 7, 8, 15, 1, tzinfo=eastern)).hour, 17)
        self.assertEqual(next_cron_time(datetime(2026, 7, 8, 17, 1, tzinfo=eastern)).day, 9)

    def test_scheduler_seconds_until_next_check(self) -> None:
        eastern = ZoneInfo("America/New_York")
        self.assertEqual(seconds_until_next_check(datetime(2026, 7, 8, 8, 59, tzinfo=eastern)), 60)
        self.assertEqual(seconds_until_next_check(datetime(2026, 7, 8, 9, 1, tzinfo=eastern)), 7140)
        self.assertEqual(seconds_until_next_check(datetime(2026, 7, 8, 11, 59, tzinfo=eastern)), 3660)


    def test_monitor_advances_state_when_wechat_send_fails(self) -> None:
        import x_wechat_relay.monitor as monitor
        import x_wechat_relay.wechat_bot as wechat_bot

        async def fake_fetch_latest_tweets():
            from x_wechat_relay.x_source import Tweet
            return [Tweet("OpenAI", "2", "new", "https://x.com/OpenAI/status/2", "Tue Jun 30 17:12:23 +0000 2026")]

        class Bot:
            async def send(self, _user_id, _text):
                raise RuntimeError("send failed")

        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()
        saved = []
        old_fetch = monitor.fetch_latest_tweets
        old_load = monitor.load_last_ids
        old_save = monitor.save_last_ids
        old_write_status = monitor.write_monitor_status
        old_path = monitor.WECHAT_BINDING_PATH
        old_status_path = wechat_bot.WECHAT_STATUS_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            binding_path = Path(tmpdir) / "binding.json"
            status_path = Path(tmpdir) / "wechat_status.json"
            save_binding(binding_path, "user-1", "ctx-1")
            monitor.fetch_latest_tweets = fake_fetch_latest_tweets
            monitor.load_last_ids = lambda: {"OpenAI": "1"}
            monitor.save_last_ids = lambda value: saved.append(value)
            monitor.write_monitor_status = lambda *_args, **_kwargs: None
            monitor.WECHAT_BINDING_PATH = binding_path
            wechat_bot.WECHAT_BINDING_PATH = binding_path
            wechat_bot.WECHAT_STATUS_PATH = status_path
            try:
                sent_count = run_check_once_from_thread(Bot(), loop)
            finally:
                loop.call_soon_threadsafe(loop.stop)
                loop_thread.join(timeout=1)
                loop.close()
                monitor.fetch_latest_tweets = old_fetch
                monitor.load_last_ids = old_load
                monitor.save_last_ids = old_save
                monitor.write_monitor_status = old_write_status
                monitor.WECHAT_BINDING_PATH = old_path
                wechat_bot.WECHAT_BINDING_PATH = old_path
                wechat_bot.WECHAT_STATUS_PATH = old_status_path

        self.assertEqual(sent_count, 0)
        self.assertEqual(saved, [{"OpenAI": "2"}])

    def test_monitor_sends_all_candidates_in_one_digest(self) -> None:
        import x_wechat_relay.monitor as monitor
        import x_wechat_relay.wechat_bot as wechat_bot
        from x_wechat_relay.x_source import Tweet

        async def fake_fetch_latest_tweets():
            return [
                Tweet("OpenAI", "3", "newest", "https://x.com/OpenAI/status/3", "Tue Jun 30 17:12:23 +0000 2026"),
                Tweet("OpenAI", "2", "middle", "https://x.com/OpenAI/status/2", "Tue Jun 30 17:11:23 +0000 2026"),
            ]

        class Bot:
            sent_text = ""

            async def send(self, _user_id, text):
                self.sent_text = text

        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()
        old_fetch = monitor.fetch_latest_tweets
        old_load = monitor.load_last_ids
        old_save = monitor.save_last_ids
        old_write_status = monitor.write_monitor_status
        old_path = monitor.WECHAT_BINDING_PATH
        old_status_path = wechat_bot.WECHAT_STATUS_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            binding_path = Path(tmpdir) / "binding.json"
            status_path = Path(tmpdir) / "wechat_status.json"
            save_binding(binding_path, "user-1", "ctx-1")
            bot = Bot()
            monitor.fetch_latest_tweets = fake_fetch_latest_tweets
            monitor.load_last_ids = lambda: {"OpenAI": "1"}
            monitor.save_last_ids = lambda _value: None
            monitor.write_monitor_status = lambda *_args, **_kwargs: None
            monitor.WECHAT_BINDING_PATH = binding_path
            wechat_bot.WECHAT_BINDING_PATH = binding_path
            wechat_bot.WECHAT_STATUS_PATH = status_path
            try:
                sent_count = run_check_once_from_thread(bot, loop)
            finally:
                loop.call_soon_threadsafe(loop.stop)
                loop_thread.join(timeout=1)
                loop.close()
                monitor.fetch_latest_tweets = old_fetch
                monitor.load_last_ids = old_load
                monitor.save_last_ids = old_save
                monitor.write_monitor_status = old_write_status
                monitor.WECHAT_BINDING_PATH = old_path
                wechat_bot.WECHAT_BINDING_PATH = old_path
                wechat_bot.WECHAT_STATUS_PATH = old_status_path

        self.assertEqual(sent_count, 2)
        self.assertIn("https://x.com/OpenAI/status/3", bot.sent_text)
        self.assertIn("https://x.com/OpenAI/status/2", bot.sent_text)


if __name__ == "__main__":
    unittest.main()
