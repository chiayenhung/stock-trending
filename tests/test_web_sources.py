from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Set

import yaml

from stocktrend.sourcing import (
    ReadOnlyMarketDataAdapter,
    SourceService,
    validate_run_input,
)
from stocktrend.util import atomic_write_json, parse_datetime
from stocktrend.web_sources import (
    EiaIndustryAdapter,
    SecEdgarNewsAdapter,
    XBrowserSnapshotAdapter,
)


SEC_HTML = """
<html><body><table class="tableFile2">
  <tr><th>Filings</th><th>Format</th><th>Description</th><th>Filing Date</th></tr>
  <tr>
    <td>8-K</td>
    <td><a href="/Archives/edgar/data/1/filing-index.htm">Documents</a></td>
    <td>Current report, item 2.02 Acc-no: 0000000001-26-000001</td>
    <td>2026-07-10</td><td>001</td>
  </tr>
  <tr>
    <td>8-K</td>
    <td><a href="/Archives/edgar/data/1/sameday-index.htm">Documents</a></td>
    <td>Same-day report Acc-no: 0000000001-26-000002</td>
    <td>2026-07-15</td><td>002</td>
  </tr>
  <tr>
    <td>4</td>
    <td><a href="/Archives/edgar/data/1/ownership-index.htm">Documents</a></td>
    <td>Ownership filing Acc-no: 0000000001-26-000003</td>
    <td>2026-07-09</td><td>003</td>
  </tr>
</table></body></html>
"""


EIA_HTML = """
<html><body>
  <div>Jul 15, 2026</div>
  <h1><a href="detail.php?id=101">Same-day electricity article</a></h1>
  <p>Electric grid generation changed today.</p>
  <div>Jul 14, 2026</div>
  <h1><a href="detail.php?id=100">Electricity demand increased</a></h1>
  <p>Power demand and electricity generation increased across the region.</p>
  <div>Jul 13, 2026</div>
  <h1><a href="detail.php?id=99">Crude oil exports increased</a></h1>
  <p>Petroleum exports rose.</p>
</body></html>
"""


def _social_snapshot(posts: list) -> Dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "capture_method": "browser",
        "account": "aleabitoreddit",
        "profile_url": "https://x.com/aleabitoreddit",
        "captured_at": "2026-07-15T20:00:00Z",
        "window_start": "2026-07-10T20:00:00Z",
        "window_end": "2026-07-15T20:00:00Z",
        "posts": posts,
    }


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
    return parse_datetime("2026-07-15T20:00:00Z")


def _enable_public_sources(project_root: Path) -> None:
    path = project_root / "spec" / "sources.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["sources"]["news"]["enabled"] = True
    value["sources"]["industry"]["enabled"] = True
    value["sources"]["social"]["enabled"] = False
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_sec_edgar_scrape_is_deterministic_and_excludes_same_day() -> None:
    captured = {}

    def transport(request, timeout):
        captured["url"] = request.full_url
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return SEC_HTML

    adapter = SecEdgarNewsAdapter(
        page_url="https://www.sec.gov/cgi-bin/browse-edgar",
        allowed_host="www.sec.gov",
        license_class="test_public_content",
        user_agent="stocktrend-test/1.0",
        transport=transport,
    )
    result = adapter.fetch_observations(
        [{"instrument_id": "US:XNAS:NVDA", "symbol": "NVDA", "venue": "XNAS"}],
        "2026-07-15",
        "2026-07-15T20:00:00Z",
    )
    assert len(result.observations) == 1
    assert result.observations[0]["value"]["form_type"] == "8-K"
    assert result.observations[0]["source"]["provider"] == "sec_edgar_html"
    assert captured["user_agent"] == "stocktrend-test/1.0"
    assert captured["timeout"] == 20
    assert "CIK=NVDA" in captured["url"]
    assert adapter.model is None


def test_eia_scrape_is_scoped_to_power_and_filters_irrelevant_articles() -> None:
    adapter = EiaIndustryAdapter(
        page_url="https://www.eia.gov/todayinenergy/",
        allowed_host="www.eia.gov",
        license_class="test_public_content",
        transport=lambda request, timeout: EIA_HTML,
    )
    result = adapter.fetch_observations(
        [
            {
                "instrument_id": "US:XNYS:VRT",
                "symbol": "VRT",
                "venue": "XNYS",
                "bucket": "power_infrastructure",
            },
            {
                "instrument_id": "US:XNAS:NVDA",
                "symbol": "NVDA",
                "venue": "XNAS",
                "bucket": "semiconductor",
            },
        ],
        "2026-07-15",
        "2026-07-15T20:00:00Z",
    )
    assert len(result.observations) == 1
    assert result.observations[0]["symbol"] == "VRT"
    assert result.observations[0]["source"]["record_id"] == "eia:100"
    assert result.observations[0]["value"]["published_date"] == "2026-07-14"
    assert adapter.model is None


def test_x_browser_snapshot_is_bounded_and_filters_exact_cashtags(
    project_root: Path,
) -> None:
    snapshot_path = project_root / "state" / "social" / "x_aleabitoreddit.json"
    atomic_write_json(
        snapshot_path,
        _social_snapshot(
            [
                {
                    "post_id": "103",
                    "url": "https://x.com/aleabitoreddit/status/103",
                    "posted_at": "2026-07-15T19:40:00Z",
                    "text": "$AAPLX is a different symbol",
                },
                {
                    "post_id": "102",
                    "url": "https://x.com/aleabitoreddit/status/102",
                    "posted_at": "2026-07-15T19:30:00Z",
                    "text": "$AAPL and $NVDA supply-chain update",
                },
                {
                    "post_id": "101",
                    "url": "https://x.com/aleabitoreddit/status/101",
                    "posted_at": "2026-07-15T18:30:00Z",
                    "text": "Watching $aapl",
                },
                {
                    "post_id": "100",
                    "url": "https://x.com/aleabitoreddit/status/100",
                    "posted_at": "2026-07-15T17:30:00Z",
                    "text": "$AAPL older",
                },
            ]
        ),
    )
    adapter = XBrowserSnapshotAdapter(
        profile_url="https://x.com/aleabitoreddit",
        account="aleabitoreddit",
        allowed_host="x.com",
        allowlist=["x.com"],
        snapshot_path=snapshot_path,
        schema_dir=project_root / "schemas",
        license_class="test_public_social_content",
        max_items_per_instrument=2,
    )
    result = adapter.fetch_observations(
        [
            {
                "instrument_id": "US:XNAS:AAPL",
                "symbol": "AAPL",
                "venue": "XNAS",
            },
            {
                "instrument_id": "US:XNAS:NVDA",
                "symbol": "NVDA",
                "venue": "XNAS",
            },
        ],
        "2026-07-15",
        "2026-07-15T20:00:00Z",
    )
    assert [
        item["source"]["record_id"]
        for item in result.observations
    ] == ["102", "102", "101"]
    assert result.observations[0]["fact_type"] == "social_post"
    assert result.observations[0]["trust"] == {
        "provenance_class": "public_social_browser",
        "extraction_method": "deterministic",
        "corroboration_state": "uncorroborated",
    }
    assert result.attempted == 1
    assert result.failed == 0
    assert adapter.model is None


def test_x_browser_snapshot_missing_and_stale_are_fail_closed(
    project_root: Path,
) -> None:
    snapshot_path = project_root / "state" / "social" / "x_aleabitoreddit.json"
    adapter = XBrowserSnapshotAdapter(
        profile_url="https://x.com/aleabitoreddit",
        account="aleabitoreddit",
        allowed_host="x.com",
        allowlist=["x.com"],
        snapshot_path=snapshot_path,
        schema_dir=project_root / "schemas",
        license_class="test_public_social_content",
    )
    result = adapter.fetch_observations(
        [
            {
                "instrument_id": "US:XNAS:AAPL",
                "symbol": "AAPL",
                "venue": "XNAS",
            },
        ],
        "2026-07-15",
        "2026-07-15T20:00:00Z",
    )
    assert result.observations == []
    assert result.attempted == 1
    assert result.failed == 1
    assert result.error_code == "SOCIAL_BROWSER_SNAPSHOT_MISSING"

    atomic_write_json(
        snapshot_path,
        _social_snapshot(
            [
                {
                    "post_id": "101",
                    "url": "https://x.com/aleabitoreddit/status/101",
                    "posted_at": "2026-07-15T19:30:00Z",
                    "text": "$AAPL update",
                }
            ]
        ),
    )
    stale = adapter.fetch_observations(
        [
            {
                "instrument_id": "US:XNAS:AAPL",
                "symbol": "AAPL",
                "venue": "XNAS",
            }
        ],
        "2026-07-15",
        "2026-07-15T20:31:00Z",
    )
    assert stale.observations == []
    assert stale.error_code == "SOCIAL_BROWSER_SNAPSHOT_STALE"


def test_source_snapshot_includes_public_web_enrichments(project_root: Path) -> None:
    _enable_public_sources(project_root)
    sec = SecEdgarNewsAdapter(
        page_url="https://www.sec.gov/cgi-bin/browse-edgar",
        allowed_host="www.sec.gov",
        license_class="us_government_public_content",
        user_agent="stocktrend-test/1.0",
        transport=lambda request, timeout: SEC_HTML,
    )
    eia = EiaIndustryAdapter(
        page_url="https://www.eia.gov/todayinenergy/",
        allowed_host="www.eia.gov",
        license_class="us_government_public_content",
        transport=lambda request, timeout: EIA_HTML,
    )
    result = SourceService(
        project_root,
        FakeMarketDataAdapter(),
        clock=_clock,
        enrichment_adapters={"news": sec, "industry": eia},
    ).run("2026-07-15")
    document = result["document"]
    enrichments = document["source_snapshot"]["enrichments"]
    assert enrichments["news"]["status"] == "success"
    assert enrichments["news"]["observation_count"] == 25
    assert enrichments["news"]["model"] is None
    assert enrichments["industry"]["status"] == "success"
    assert enrichments["industry"]["observation_count"] == 5
    assert enrichments["social"]["status"] == "disabled"
    assert len(document["observations"]) == 80
    assert validate_run_input(
        project_root,
        document,
        "production",
        now=_clock(),
    ) == []


def test_enabled_social_without_allowlist_is_explicitly_degraded(
    project_root: Path,
) -> None:
    path = project_root / "spec" / "sources.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["sources"]["social"]["enabled"] = True
    value["sources"]["social"]["allowlist"] = []
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    result = SourceService(
        project_root,
        FakeMarketDataAdapter(),
        clock=_clock,
        enrichment_adapters={},
    ).run("2026-07-15")
    social = result["document"]["source_snapshot"]["enrichments"]["social"]
    assert social["status"] == "unavailable"
    assert social["error_code"] == "SOCIAL_ALLOWLIST_EMPTY"
    assert validate_run_input(
        project_root,
        result["document"],
        "production",
        now=_clock(),
    ) == ["SOCIAL_SOURCE_UNAVAILABLE"]


def test_source_snapshot_includes_x_browser_social_enrichment(
    project_root: Path,
) -> None:
    path = project_root / "spec" / "sources.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["sources"]["social"]["enabled"] = True
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    universe = yaml.safe_load(
        (project_root / "spec" / "universe.yaml").read_text(encoding="utf-8")
    )
    symbols = [item["symbol"] for item in universe["instruments"]]
    snapshot_path = project_root / "state" / "social" / "x_aleabitoreddit.json"
    atomic_write_json(
        snapshot_path,
        _social_snapshot(
            [
                {
                    "post_id": "101",
                    "url": "https://x.com/aleabitoreddit/status/101",
                    "posted_at": "2026-07-15T19:30:00Z",
                    "text": " ".join("$%s" % symbol for symbol in symbols),
                }
            ]
        ),
    )
    social = XBrowserSnapshotAdapter(
        profile_url="https://x.com/aleabitoreddit",
        account="aleabitoreddit",
        allowed_host="x.com",
        allowlist=["x.com"],
        snapshot_path=snapshot_path,
        schema_dir=project_root / "schemas",
        license_class="public_social_browser_content",
    )
    result = SourceService(
        project_root,
        FakeMarketDataAdapter(),
        clock=_clock,
        enrichment_adapters={"social": social},
    ).run("2026-07-15")
    document = result["document"]
    social_status = document["source_snapshot"]["enrichments"]["social"]
    assert social_status["status"] == "success"
    assert social_status["observation_count"] == 25
    assert social_status["attempted"] == 1
    assert len(
        [
            item
            for item in document["observations"]
            if item["fact_type"] == "social_post"
        ]
    ) == 25
    assert validate_run_input(
        project_root,
        document,
        "production",
        now=_clock(),
    ) == []
