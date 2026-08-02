from __future__ import annotations

import unittest

import x_wechat_relay.monitor as monitor


class ConnectError(Exception):
    pass


class ConnectTimeout(Exception):
    pass


class SSLEOFError(Exception):
    pass


class ApiError(Exception):
    pass


def initialization_error(cause: Exception) -> RuntimeError:
    try:
        raise cause
    except Exception as exc:
        try:
            raise RuntimeError("stage=client_initialization host=x.com") from exc
        except RuntimeError as wrapped:
            return wrapped


class MonitorRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_fetch = monitor.fetch_latest_tweets
        self.old_write_status = monitor.write_monitor_status
        self.events = []
        monitor.write_monitor_status = lambda event, **fields: self.events.append((event, fields))

    def tearDown(self) -> None:
        monitor.fetch_latest_tweets = self.old_fetch
        monitor.write_monitor_status = self.old_write_status

    def test_retry_delay_range_is_bounded(self) -> None:
        self.assertEqual(monitor.X_RETRY_DELAY_MIN_SECONDS, 20.0)
        self.assertEqual(monitor.X_RETRY_DELAY_MAX_SECONDS, 60.0)

    def test_supported_initialization_connection_errors_are_retryable(self) -> None:
        for error_type in (ConnectError, ConnectTimeout, SSLEOFError):
            with self.subTest(error_type=error_type.__name__):
                self.assertTrue(monitor.is_retryable_x_initialization_error(initialization_error(error_type("failed"))))

    def test_initialization_connection_error_retries_once(self) -> None:
        calls = 0

        async def fake_fetch():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise initialization_error(ConnectError("connection closed"))
            return ["tweet"]

        monitor.fetch_latest_tweets = fake_fetch

        result = monitor.asyncio.run(monitor.fetch_latest_tweets_with_retry(retry_delay_seconds=0))

        self.assertEqual(result, ["tweet"])
        self.assertEqual(calls, 2)
        self.assertEqual([event for event, _fields in self.events], ["x_retry_waiting", "fetching_x", "x_retry_succeeded"])

    def test_second_initialization_connection_error_is_returned_to_caller(self) -> None:
        calls = 0

        async def fake_fetch():
            nonlocal calls
            calls += 1
            raise initialization_error(ConnectError("connection closed"))

        monitor.fetch_latest_tweets = fake_fetch

        with self.assertRaises(RuntimeError):
            monitor.asyncio.run(monitor.fetch_latest_tweets_with_retry(retry_delay_seconds=0))

        self.assertEqual(calls, 2)
        self.assertEqual([event for event, _fields in self.events], ["x_retry_waiting", "fetching_x"])

    def test_non_initialization_connection_error_does_not_retry(self) -> None:
        calls = 0

        async def fake_fetch():
            nonlocal calls
            calls += 1
            raise RuntimeError("stage=read_timeline host=x.com") from ConnectError("connection closed")

        monitor.fetch_latest_tweets = fake_fetch

        with self.assertRaises(RuntimeError):
            monitor.asyncio.run(monitor.fetch_latest_tweets_with_retry(retry_delay_seconds=0))

        self.assertEqual(calls, 1)
        self.assertEqual(self.events, [])

    def test_non_connection_initialization_error_does_not_retry(self) -> None:
        calls = 0

        async def fake_fetch():
            nonlocal calls
            calls += 1
            raise initialization_error(ApiError("forbidden"))

        monitor.fetch_latest_tweets = fake_fetch

        with self.assertRaises(RuntimeError):
            monitor.asyncio.run(monitor.fetch_latest_tweets_with_retry(retry_delay_seconds=0))

        self.assertEqual(calls, 1)
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
