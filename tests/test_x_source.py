from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from x_wechat_relay.x_source import DEFAULT_HANDLES, DEFAULT_TWEET_COUNT, X_HANDLES_ENV, X_REQUEST_TIMEOUT_SECONDS, X_STATIC_ASSET_FALLBACK_IPS, Tweet, configured_handles, format_tweet_message, format_x_connection_diagnostic, normalize_tweets, parse_handles, save_handles_file, summarize_tweets, tweet_sort_key, x_fallback_getaddrinfo
from x_wechat_relay.x_state import plan_new_tweets


@dataclass(frozen=True)
class FakeTwikitTweet:
    id: str
    text: str
    created_at: str
    full_text: str | None = None


class XSourceTest(unittest.TestCase):
    def test_x_dns_fallback_replaces_poisoned_static_asset_resolution(self) -> None:
        calls = []

        def original(host, port, family, type, proto, flags):
            calls.append(host)
            return [(family, type, proto, "", (host, port))]

        results = x_fallback_getaddrinfo(original, "abs.twimg.com", 443)

        self.assertEqual(calls, list(X_STATIC_ASSET_FALLBACK_IPS))
        self.assertEqual([result[4][0] for result in results], list(X_STATIC_ASSET_FALLBACK_IPS))

    def test_x_dns_fallback_accepts_byte_hostname_from_httpcore(self) -> None:
        calls = []

        def original(host, port, family, type, proto, flags):
            calls.append(host)
            return []

        x_fallback_getaddrinfo(original, b"abs.twimg.com", 443)

        self.assertEqual(calls, list(X_STATIC_ASSET_FALLBACK_IPS))

    def test_x_dns_fallback_records_safe_connection_diagnostic(self) -> None:
        def original(host, port, family, type, proto, flags):
            return [(family, type, proto, "", (host, port))]

        x_fallback_getaddrinfo(original, "abs.twimg.com", 443)

        self.assertEqual(
            format_x_connection_diagnostic("client_initialization", "abs.twimg.com"),
            "stage=client_initialization host=abs.twimg.com dns_route=fallback resolved_ips=104.18.39.59,172.64.148.197",
        )

    def test_x_dns_fallback_leaves_other_hosts_unchanged(self) -> None:
        calls = []

        def original(host, port, family, type, proto, flags):
            calls.append(host)
            return []

        self.assertEqual(x_fallback_getaddrinfo(original, "x.com", 443), [])
        self.assertEqual(calls, ["x.com"])

    def test_default_tweet_count_is_three(self) -> None:
        self.assertEqual(DEFAULT_TWEET_COUNT, 3)

    def test_x_request_timeout_is_short_enough_for_cron_window(self) -> None:
        self.assertEqual(X_REQUEST_TIMEOUT_SECONDS, 30.0)

    def test_configured_handles_uses_default_accounts(self) -> None:
        self.assertEqual(configured_handles({}), DEFAULT_HANDLES)

    def test_parse_handles_accepts_commas_at_prefix_and_deduplicates(self) -> None:
        self.assertEqual(parse_handles("@OpenAI, AnthropicAI, claudeai, OpenAI"), ("OpenAI", "AnthropicAI", "claudeai"))

    def test_configured_handles_reads_env_accounts(self) -> None:
        self.assertEqual(configured_handles({X_HANDLES_ENV: "OpenAI, AnthropicAI, claudeai"}), ("OpenAI", "AnthropicAI", "claudeai"))

    def test_configured_handles_reads_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "x_handles.txt"
            path.write_text("OpenAI\nAnthropicAI\n", encoding="utf-8")

            self.assertEqual(configured_handles({}, path), ("OpenAI", "AnthropicAI"))

    def test_save_handles_file_writes_normalized_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "x_handles.txt"

            saved_path = save_handles_file("@OpenAI, AnthropicAI, claudeai, OpenAI", path)

            self.assertEqual(saved_path, path)
            self.assertEqual(path.read_text(encoding="utf-8"), "OpenAI\nAnthropicAI\nclaudeai\n")

    def test_normalize_tweets_keeps_supported_fields(self) -> None:
        tweets = normalize_tweets(
            "OpenAI",
            [FakeTwikitTweet("2072004836674167294", "We are introducing GeneBench-Pro.", "Tue Jun 30 17:10:23 +0000 2026")],
        )

        self.assertEqual(tweets[0].account, "OpenAI")
        self.assertEqual(tweets[0].id, "2072004836674167294")
        self.assertEqual(tweets[0].url, "https://x.com/OpenAI/status/2072004836674167294")
        self.assertEqual(tweets[0].text, "We are introducing GeneBench-Pro.")

    def test_normalize_tweets_sorts_by_created_at_desc(self) -> None:
        tweets = normalize_tweets(
            "OpenAI",
            [
                FakeTwikitTweet("1", "older", "Tue Jun 30 17:10:23 +0000 2026"),
                FakeTwikitTweet("3", "newer", "Tue Jun 30 17:12:23 +0000 2026"),
                FakeTwikitTweet("2", "middle", "Tue Jun 30 17:11:23 +0000 2026"),
            ],
        )

        self.assertEqual([tweet.id for tweet in tweets], ["3", "2", "1"])

    def test_tweet_sort_key_handles_iso_datetime(self) -> None:
        tweet = Tweet("OpenAI", "1", "text", "https://x.com/OpenAI/status/1", "2026-06-30T17:10:23Z")

        self.assertEqual(tweet_sort_key(tweet).year, 2026)

    def test_normalize_tweets_prefers_full_text(self) -> None:
        tweets = normalize_tweets(
            "AnthropicAI",
            [FakeTwikitTweet("2072163884430229756", "truncated", "Wed Jul 01 03:42:23 +0000 2026", "full text")],
        )

        self.assertEqual(tweets[0].text, "full text")

    def test_format_tweet_message_uses_three_lines(self) -> None:
        tweet = Tweet(
            account="AnthropicAI",
            id="2072163884430229756",
            text="Claude Fable 5 will be available again globally tomorrow.",
            url="https://x.com/AnthropicAI/status/2072163884430229756",
            created_at="Wed Jul 01 03:42:23 +0000 2026",
        )

        self.assertEqual(
            format_tweet_message(tweet),
            "AnthropicAI\nClaude Fable 5 will be available again globally tomorrow.\nhttps://x.com/AnthropicAI/status/2072163884430229756",
        )

    def test_format_tweet_message_compacts_tweet_text_newlines(self) -> None:
        tweet = Tweet(
            account="OpenAI",
            id="1",
            text="line one\nline two",
            url="https://x.com/OpenAI/status/1",
            created_at="Tue Jun 30 17:10:23 +0000 2026",
        )

        self.assertEqual(format_tweet_message(tweet).splitlines(), ["OpenAI", "line one line two", "https://x.com/OpenAI/status/1"])

    def test_summarize_tweets_counts_per_account(self) -> None:
        tweets = normalize_tweets(
            "OpenAI",
            [
                FakeTwikitTweet("1", "one", "Tue Jun 30 17:10:23 +0000 2026"),
                FakeTwikitTweet("2", "two", "Tue Jun 30 17:11:23 +0000 2026"),
            ],
        )

        self.assertEqual(summarize_tweets(tweets), {"OpenAI": 2})

    def test_state_plans_new_tweets_by_tweet_id(self) -> None:
        tweets = normalize_tweets(
            "AnthropicAI",
            [FakeTwikitTweet("2072163884430229756", "new", "Wed Jul 01 03:42:23 +0000 2026")],
        )
        last_ids = {"AnthropicAI": "2072163884430229755", "OpenAI": "2072004836674167294"}

        candidates, next_ids = plan_new_tweets(tweets, last_ids)

        self.assertEqual([tweet.id for tweet in candidates], ["2072163884430229756"])
        self.assertEqual(next_ids["AnthropicAI"], "2072163884430229756")

    def test_state_keeps_latest_tweet_id_when_multiple_tweets_return(self) -> None:
        tweets = normalize_tweets(
            "OpenAI",
            [
                FakeTwikitTweet("3", "newest", "Tue Jun 30 17:12:23 +0000 2026"),
                FakeTwikitTweet("2", "middle", "Tue Jun 30 17:11:23 +0000 2026"),
                FakeTwikitTweet("1", "old", "Tue Jun 30 17:10:23 +0000 2026"),
            ],
        )

        candidates, next_ids = plan_new_tweets(tweets, {"OpenAI": "1"})

        self.assertEqual([tweet.id for tweet in candidates], ["3", "2"])
        self.assertEqual(next_ids["OpenAI"], "3")

    def test_state_stops_after_seen_tweet_id(self) -> None:
        tweets = normalize_tweets(
            "OpenAI",
            [
                FakeTwikitTweet("3", "new", "Tue Jun 30 17:12:23 +0000 2026"),
                FakeTwikitTweet("2", "seen", "Tue Jun 30 17:11:23 +0000 2026"),
                FakeTwikitTweet("1", "older", "Tue Jun 30 17:10:23 +0000 2026"),
            ],
        )

        candidates, next_ids = plan_new_tweets(tweets, {"OpenAI": "2"})

        self.assertEqual([tweet.id for tweet in candidates], ["3"])
        self.assertEqual(next_ids["OpenAI"], "3")

    def test_state_skips_already_seen_tweet_id(self) -> None:
        tweets = normalize_tweets(
            "OpenAI",
            [FakeTwikitTweet("2072004836674167294", "old", "Tue Jun 30 17:10:23 +0000 2026")],
        )

        candidates, next_ids = plan_new_tweets(tweets, {"OpenAI": "2072004836674167294"})

        self.assertEqual(candidates, [])
        self.assertEqual(next_ids["OpenAI"], "2072004836674167294")


if __name__ == "__main__":
    unittest.main()
