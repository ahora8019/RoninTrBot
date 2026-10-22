from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from app.market_data.lbank_async import AsyncLBankMarketDataClient, AsyncMarketDataError


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.headers = {"content-length": str(len(json.dumps(payload)))}
        self._raw = json.dumps(payload)

    async def text(self):
        return self._raw


def test_async_lbank_parses_and_validates_public_candles():
    async def run():
        now = 1_800_000_000
        # Use timestamps aligned to the hour and a fresh last candle.
        now = now - (now % 3600)
        rows = [
            [now - 7200, "100", "110", "90", "105", "12.5"],
            [now - 3600, "105", "112", "101", "108", "15"],
        ]

        async def fetcher(url, options):
            assert "symbol=btc_usdt" in url
            assert "type=hour1" in url
            assert options["headers"]["Accept"] == "application/json"
            return FakeResponse({"result": True, "error_code": 0, "data": rows})

        client = AsyncLBankMarketDataClient(fetcher, clock=lambda: now, sleep=lambda _: asyncio.sleep(0))
        candles = await client.fetch_ohlcv("BTC_USDT", "1h", 2)
        assert len(candles) == 2
        assert candles[0].close == Decimal("105")
        assert candles[1].volume == Decimal("15")
    asyncio.run(run())


def test_async_lbank_rejects_bad_ohlc_and_invalid_symbol():
    async def run():
        async def fetcher(url, options):
            return FakeResponse({"result": True, "data": [[1800000000, "10", "5", "8", "7", "1"]]})
        client = AsyncLBankMarketDataClient(fetcher, clock=lambda: 1800003600)
        with pytest.raises(ValueError):
            await client.fetch_ohlcv("BTCUSDT", "1h", 1)
        with pytest.raises(AsyncMarketDataError):
            await client.fetch_ohlcv("btc_usdt", "1h", 1)
    asyncio.run(run())


def test_async_lbank_retries_transient_http_failure():
    async def run():
        calls = []
        now = 1_800_000_000
        now -= now % 3600
        rows = [[now - 3600, "10", "11", "9", "10.5", "1"]]
        async def fetcher(url, options):
            calls.append(url)
            if len(calls) == 1:
                return FakeResponse({}, status=503)
            return FakeResponse({"result": True, "data": rows})
        async def no_sleep(_):
            return None
        client = AsyncLBankMarketDataClient(fetcher, clock=lambda: now, sleep=no_sleep)
        result = await client.fetch_ohlcv("btc_usdt", "1h", 1)
        assert len(result) == 1
        assert len(calls) == 2
    asyncio.run(run())

def test_async_lbank_rejects_duplicate_candle_timestamps():
    async def run():
        now = 1_800_000_000
        now -= now % 3600
        row = [now - 3600, "10", "11", "9", "10.5", "1"]

        async def fetcher(url, options):
            return FakeResponse({"result": True, "error_code": 0, "data": [row, row]})

        client = AsyncLBankMarketDataClient(
            fetcher, clock=lambda: now, sleep=lambda _: asyncio.sleep(0)
        )
        with pytest.raises(AsyncMarketDataError, match="duplicate timestamps"):
            await client.fetch_ohlcv("btc_usdt", "1h", 2)

    asyncio.run(run())


def test_async_lbank_rejects_gaps_in_candle_series():
    async def run():
        now = 1_800_000_000
        now -= now % 3600
        rows = [
            [now - 3 * 3600, "10", "11", "9", "10.5", "1"],
            [now - 3600, "10.5", "12", "10", "11", "2"],
        ]

        async def fetcher(url, options):
            return FakeResponse({"result": True, "error_code": 0, "data": rows})

        client = AsyncLBankMarketDataClient(
            fetcher, clock=lambda: now, sleep=lambda _: asyncio.sleep(0)
        )
        with pytest.raises(AsyncMarketDataError, match="gap in candle series"):
            await client.fetch_ohlcv("btc_usdt", "1h", 2)

    asyncio.run(run())



def test_async_lbank_rejects_api_error_payload():
    async def run():
        async def fetcher(url, options):
            return FakeResponse({"result": False, "error_code": 10005, "data": []})

        client = AsyncLBankMarketDataClient(
            fetcher, clock=lambda: 1_800_000_000, sleep=lambda _: asyncio.sleep(0)
        )
        with pytest.raises(AsyncMarketDataError, match="API error"):
            await client.fetch_ohlcv("btc_usdt", "1h", 1)

    asyncio.run(run())


def test_async_lbank_rejects_missing_candle_data():
    async def run():
        async def fetcher(url, options):
            return FakeResponse({"result": True, "error_code": 0})

        client = AsyncLBankMarketDataClient(
            fetcher, clock=lambda: 1_800_000_000, sleep=lambda _: asyncio.sleep(0)
        )
        with pytest.raises(AsyncMarketDataError, match="missing candle data"):
            await client.fetch_ohlcv("btc_usdt", "1h", 1)

    asyncio.run(run())
