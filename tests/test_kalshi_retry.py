"""A 24h tape pull is thousands of GETs. A single 429 used to kill the run."""

from __future__ import annotations

import io
import unittest
import urllib.error
from unittest.mock import patch

from btchour.kalshi import KalshiClient, KalshiError


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError(
        "https://x/y", code, "boom", headers, io.BytesIO(b'{"error":"x"}')
    )


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class KalshiRetryTest(unittest.TestCase):
    def setUp(self):
        self.client = KalshiClient(min_interval=0.0)
        self.client.retry_base = 0.0
        self.client.retry_cap = 0.0

    def test_get_retries_429_then_succeeds(self):
        responses = [_http_error(429), _http_error(429), _Resp(b'{"ok": true}')]

        def fake_open(req, timeout=None):
            item = responses.pop(0)
            if isinstance(item, urllib.error.HTTPError):
                raise item
            return item

        with patch("urllib.request.urlopen", side_effect=fake_open):
            self.assertEqual(self.client.get("/exchange/status"), {"ok": True})
        self.assertEqual(self.client.retry_count, 2)

    def test_get_retries_5xx(self):
        responses = [_http_error(503), _Resp(b'{"ok": 1}')]

        def fake_open(req, timeout=None):
            item = responses.pop(0)
            if isinstance(item, urllib.error.HTTPError):
                raise item
            return item

        with patch("urllib.request.urlopen", side_effect=fake_open):
            self.assertEqual(self.client.get("/x"), {"ok": 1})

    def test_get_does_not_retry_4xx_other_than_429(self):
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: (_ for _ in ()).throw(_http_error(404))):
            with self.assertRaises(KalshiError) as ctx:
                self.client.get("/missing")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(self.client.retry_count, 0)

    def test_get_gives_up_after_max_retries(self):
        self.client.max_retries = 2
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: (_ for _ in ()).throw(_http_error(429))):
            with self.assertRaises(KalshiError) as ctx:
                self.client.get("/x")
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(self.client.retry_count, 2)

    def test_signed_post_never_retries(self):
        """Resending an order could double a position. 429 must surface."""
        client = KalshiClient(min_interval=0.0)
        calls = []

        def fake_open(req, timeout=None):
            calls.append(req)
            raise _http_error(429)

        with patch.object(KalshiClient, "_sign_headers", return_value={}):
            with patch("urllib.request.urlopen", side_effect=fake_open):
                with self.assertRaises(KalshiError):
                    client.post("/portfolio/orders", {"ticker": "X"})
        self.assertEqual(len(calls), 1)

    def test_retry_after_header_is_honoured(self):
        from btchour.kalshi import _retry_after

        self.assertEqual(_retry_after(_http_error(429, "3"), 0.5), 3.0)
        self.assertEqual(_retry_after(_http_error(429, "junk"), 0.5), 0.5)
        self.assertEqual(_retry_after(_http_error(429), 0.75), 0.75)


if __name__ == "__main__":
    unittest.main()
