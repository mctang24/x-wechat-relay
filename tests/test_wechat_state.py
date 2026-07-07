from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from x_wechat_relay.wechat_bot import bot_identity_ids, format_warning, is_bot_self, refresh_binding_context, resolve_send_user_id
from x_wechat_relay.scheduler import RANDOM_WINDOW_SECONDS, next_base_check, seconds_until_next_check
from x_wechat_relay.monitor import run_check_once, send_to_binding
from datetime import datetime
from zoneinfo import ZoneInfo
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

            self.assertEqual(set(data), {"user_id", "context_token", "bound_at"})

    def test_refresh_binding_context_updates_bound_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "binding.json"
            save_binding(path, "user-1", "old")

            self.assertTrue(refresh_binding_context("user-1", "new", path))
            self.assertEqual(load_binding(path).context_token, "new")

    def test_refresh_binding_context_ignores_other_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "binding.json"
            save_binding(path, "user-1", "old")

            self.assertFalse(refresh_binding_context("user-2", "new", path))
            self.assertEqual(load_binding(path).context_token, "old")


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

    def test_scheduler_base_points_skip_weekends(self) -> None:
        eastern = ZoneInfo("America/New_York")
        self.assertEqual(next_base_check(datetime(2026, 7, 6, 7, 59, tzinfo=eastern)).hour, 8)
        self.assertEqual(next_base_check(datetime(2026, 7, 6, 8, 6, tzinfo=eastern)).hour, 10)
        self.assertEqual(next_base_check(datetime(2026, 7, 11, 8, 10, tzinfo=eastern)).weekday(), 0)

    def test_scheduler_waits_until_next_weekday_base_point(self) -> None:
        eastern = ZoneInfo("America/New_York")
        self.assertEqual(seconds_until_next_check(datetime(2026, 7, 6, 7, 59, tzinfo=eastern)), 60)
        self.assertEqual(seconds_until_next_check(datetime(2026, 7, 6, 8, 6, tzinfo=eastern)), 6840)
        self.assertEqual(seconds_until_next_check(datetime(2026, 7, 10, 18, 16, tzinfo=eastern)), 222240)

    def test_scheduler_jitter_window_size(self) -> None:
        self.assertEqual(RANDOM_WINDOW_SECONDS, 900)

    def test_monitor_send_helper_exists(self) -> None:
        self.assertIsNotNone(send_to_binding)


    def test_monitor_send_helper_returns_false_for_self_binding(self) -> None:
        import x_wechat_relay.monitor as monitor

        class Credentials:
            account_id = "bot-1"
            user_id = "login-user"

        class Bot:
            _credentials = Credentials()

            async def send(self, _user_id, _text):
                raise AssertionError("must not send to bot self")

        old_path = monitor.WECHAT_BINDING_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            binding_path = Path(tmpdir) / "binding.json"
            save_binding(binding_path, "bot-1", "ctx")
            monitor.WECHAT_BINDING_PATH = binding_path
            try:
                sent = __import__("asyncio").run(send_to_binding(Bot(), "text"))
            finally:
                monitor.WECHAT_BINDING_PATH = old_path

        self.assertFalse(sent)

    def test_monitor_advances_state_when_wechat_send_fails(self) -> None:
        import x_wechat_relay.monitor as monitor

        async def fake_fetch_latest_tweets():
            from x_wechat_relay.x_source import Tweet
            return [Tweet("OpenAI", "2", "new", "https://x.com/OpenAI/status/2", "Tue Jun 30 17:12:23 +0000 2026")]

        async def fake_sleep(_seconds):
            return None

        class Bot:
            async def send(self, _user_id, _text):
                raise RuntimeError("send failed")

        saved = []
        old_fetch = monitor.fetch_latest_tweets
        old_load = monitor.load_last_ids
        old_save = monitor.save_last_ids
        old_sleep = monitor.asyncio.sleep
        old_path = monitor.WECHAT_BINDING_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            binding_path = Path(tmpdir) / "binding.json"
            save_binding(binding_path, "user-1", "ctx-1")
            monitor.fetch_latest_tweets = fake_fetch_latest_tweets
            monitor.load_last_ids = lambda: {"OpenAI": "1"}
            monitor.save_last_ids = lambda value: saved.append(value)
            monitor.asyncio.sleep = fake_sleep
            monitor.WECHAT_BINDING_PATH = binding_path
            try:
                sent_count = __import__("asyncio").run(run_check_once(Bot()))
            finally:
                monitor.fetch_latest_tweets = old_fetch
                monitor.load_last_ids = old_load
                monitor.save_last_ids = old_save
                monitor.asyncio.sleep = old_sleep
                monitor.WECHAT_BINDING_PATH = old_path

        self.assertEqual(sent_count, 0)
        self.assertEqual(saved, [{"OpenAI": "2"}])


if __name__ == "__main__":
    unittest.main()
