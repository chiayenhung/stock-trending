from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Set

import pytest
import yaml

from stocktrend.config import ConfigBundle
from stocktrend.errors import ConfigurationError, SafetyViolation
from stocktrend.facts import FactsBuilder
from stocktrend.screening import screen_candidates
from stocktrend.sourcing import (
    HttpJsonGatewayAdapter,
    ReadOnlyMarketDataAdapter,
    SourceAdapterError,
    SourceService,
    TiingoMarketDataAdapter,
    create_source_adapter,
    source_status,
    validate_run_input,
)
from stocktrend.util import load_json, parse_datetime


NOW_TEXT = "2026-07-15T20:00:00Z"


class FakeMarketDataAdapter(ReadOnlyMarketDataAdapter):
    adapter_id = "tiingo_market_data"
    source_url = "https://api.tiingo.com"
    license_class = "test_market_data"

    def __init__(self, fail_symbols: Optional[Set[str]] = None):
        self.fail_symbols = fail_symbols or set()

    def fetch_market_record(
        self,
        instrument: Mapping[str, Any],
        session_date: str,
    ) -> Dict[str, Any]:
        del session_date
        symbol = str(instrument["symbol"])
        if symbol in self.fail_symbols:
            raise SourceAdapterError("SOURCE_REQUIRED_FACTS_MISSING")
        return {
            "quote": {
                "record_id": "%s-quote" % symbol,
                "observed_at": "2026-07-15T19:59:30Z",
                "price": 100.0,
                "bid": 99.99,
                "ask": 100.01,
            },
            "bar_metrics": {
                "record_id": "%s-metrics" % symbol,
                "observed_at": "2026-07-15T19:59:00Z",
                "average_volume_20d": 1_000_000.0,
                "volume_ratio": 1.5,
                "momentum_20d_pct": 5.0,
            },
        }


def _clock():
    return parse_datetime(NOW_TEXT)


def _enable_approved_adapter(project_root: Path) -> None:
    path = project_root / "spec" / "sources.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    approved = value["production"]["approved_adapter"]
    value["adapters"][approved]["enabled"] = True
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_source_service_builds_ready_four_bucket_snapshot(project_root: Path) -> None:
    result = SourceService(
        project_root,
        FakeMarketDataAdapter(),
        clock=_clock,
    ).run("2026-07-15")
    document = result["document"]
    metadata = document["source_snapshot"]
    assert metadata["coverage_status"] == "ready"
    assert metadata["coverage"]["valid_total"] == 25
    assert len(document["observations"]) == 50
    assert set(metadata["coverage"]["buckets"]) == {
        "semiconductor",
        "memory_storage",
        "power_infrastructure",
        "software",
    }
    assert all(
        item["valid"] >= 4
        for item in metadata["coverage"]["buckets"].values()
    )
    assert Path(result["snapshot_path"]).exists()
    assert result["heartbeat"]["status"] == "success"


def test_production_snapshot_validation_and_balanced_screen(project_root: Path) -> None:
    _enable_approved_adapter(project_root)
    result = SourceService(
        project_root,
        FakeMarketDataAdapter(),
        clock=_clock,
    ).run("2026-07-15")
    document = result["document"]
    assert validate_run_input(
        project_root,
        document,
        "production",
        now=_clock(),
    ) == []
    facts = FactsBuilder().build("run_source_test", document["observations"], NOW_TEXT)
    candidates = screen_candidates(
        facts,
        ConfigBundle.load(project_root).strategy,
        document["source_snapshot"]["instrument_buckets"],
    )
    assert len(candidates) == 12
    assert {item["industry_bucket"] for item in candidates} == {
        "semiconductor",
        "memory_storage",
        "power_infrastructure",
        "software",
    }
    assert all(
        sum(1 for item in candidates if item["industry_bucket"] == bucket) == 3
        for bucket in document["source_snapshot"]["coverage"]["buckets"]
    )


def test_incomplete_coverage_is_research_only_not_silently_ready(
    project_root: Path,
) -> None:
    universe = ConfigBundle.load(project_root).universe
    keep = {
        next(
            item["symbol"]
            for item in universe["instruments"]
            if item["bucket"] == bucket
        )
        for bucket in universe["required_buckets"]
    }
    fail = {item["symbol"] for item in universe["instruments"]} - keep
    _enable_approved_adapter(project_root)
    result = SourceService(
        project_root,
        FakeMarketDataAdapter(fail),
        clock=_clock,
    ).run("2026-07-15")
    assert result["document"]["source_snapshot"]["coverage_status"] == "incomplete"
    assert result["heartbeat"]["status"] == "incomplete"
    assert validate_run_input(
        project_root,
        result["document"],
        "production",
        now=_clock(),
    ) == ["SOURCE_COVERAGE_INCOMPLETE"]


def test_stale_source_heartbeat_blocks_production(project_root: Path) -> None:
    _enable_approved_adapter(project_root)
    result = SourceService(
        project_root,
        FakeMarketDataAdapter(),
        clock=_clock,
    ).run("2026-07-15")
    stale_now = _clock() + timedelta(seconds=901)
    assert source_status(project_root, now=stale_now)["stale"] is True
    with pytest.raises(SafetyViolation, match="stale"):
        validate_run_input(
            project_root,
            result["document"],
            "production",
            now=stale_now,
        )


def test_fixture_is_rejected_by_production_profile(project_root: Path) -> None:
    document = load_json(project_root / "tests" / "fixtures" / "demo_observations.json")
    with pytest.raises(SafetyViolation, match="fixture"):
        validate_run_input(project_root, document, "production", now=_clock())
    assert validate_run_input(project_root, document, "test", now=_clock()) == [
        "NON_PRODUCTION_INPUT"
    ]


def test_gateway_adapter_uses_header_token_and_normalizes_numeric_data() -> None:
    captured = {}

    def transport(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return {
            "schema_version": "1.0.0",
            "symbol": "NVDA",
            "quote": {
                "record_id": "quote-1",
                "observed_at": "2026-07-15T19:59:30Z",
                "price": 100,
                "bid": 99.9,
                "ask": 100.1,
            },
            "bar_metrics": {
                "record_id": "metrics-1",
                "observed_at": "2026-07-15T19:59:00Z",
                "average_volume_20d": 1000000,
                "volume_ratio": 1.5,
                "momentum_20d_pct": 5,
            },
        }

    adapter = HttpJsonGatewayAdapter(
        "https://market.example.com/v1/snapshot",
        "market.example.com",
        "secret-token",
        "test",
        transport=transport,
    )
    record = adapter.fetch_market_record(
        {"symbol": "NVDA"},
        "2026-07-15",
    )
    assert record["quote"]["price"] == 100.0
    assert "secret-token" not in captured["url"]
    assert captured["authorization"] == "Bearer secret-token"
    assert captured["timeout"] == 30


@pytest.mark.parametrize(
    ("current_volume", "expected_volume_ratio"),
    [(1_500.0, 1.5), (0.0, 0.0)],
)
def test_tiingo_adapter_uses_header_token_and_derives_metrics(
    current_volume: float,
    expected_volume_ratio: float,
) -> None:
    captured = []
    history = []
    first_day = date(2026, 6, 25)
    for index in range(20):
        history.append(
            {
                "date": (first_day + timedelta(days=index)).isoformat(),
                "adjClose": 100.0 + index,
                "adjVolume": 1_000.0,
            }
        )

    def transport(request, timeout):
        captured.append(
            {
                "url": request.full_url,
                "authorization": request.get_header("Authorization"),
                "timeout": timeout,
            }
        )
        if "/equity/intraday/" in request.full_url:
            return [
                {
                    "ticker": "NVDA",
                    "timestamp": "2026-07-15T15:59:30.123456789-04:00",
                    "tngoLast": 120.0,
                    "lqBidPrice": 119.9,
                    "lqAskPrice": 120.1,
                    "volume": current_volume,
                }
            ]
        return history

    adapter = TiingoMarketDataAdapter(
        "https://api.tiingo.com",
        "api.tiingo.com",
        "tiingo-secret",
        "test",
        transport=transport,
    )
    record = adapter.fetch_market_record({"symbol": "NVDA"}, "2026-07-15")
    assert record["quote"]["price"] == 120.0
    assert parse_datetime(record["quote"]["observed_at"]).microsecond == 123456
    assert record["bar_metrics"]["average_volume_20d"] == 1_000.0
    assert record["bar_metrics"]["volume_ratio"] == expected_volume_ratio
    assert record["bar_metrics"]["momentum_20d_pct"] == pytest.approx(20.0)
    assert len(captured) == 2
    assert all(item["authorization"] == "Token tiingo-secret" for item in captured)
    assert all("tiingo-secret" not in item["url"] for item in captured)
    assert all(item["timeout"] == 30 for item in captured)


def test_configured_tiingo_adapter_requires_token(
    project_root: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("STOCKTREND_TIINGO_API_TOKEN", raising=False)
    with pytest.raises(ConfigurationError, match="token"):
        create_source_adapter(project_root)


def test_disabled_approved_adapter_fails_closed(project_root: Path) -> None:
    path = project_root / "spec" / "sources.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    approved = value["production"]["approved_adapter"]
    value["adapters"][approved]["enabled"] = False
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="disabled"):
        create_source_adapter(project_root)


def test_stale_universe_review_blocks_sourcing(project_root: Path) -> None:
    with pytest.raises(SafetyViolation, match="universe review is stale"):
        SourceService(
            project_root,
            FakeMarketDataAdapter(),
            clock=lambda: parse_datetime("2027-01-01T20:00:00Z"),
        ).run("2027-01-01")
