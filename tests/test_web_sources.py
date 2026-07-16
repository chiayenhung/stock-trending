from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Set

import yaml

from stocktrend.sourcing import (
    ReadOnlyMarketDataAdapter,
    SourceService,
    validate_run_input,
)
from stocktrend.util import parse_datetime
from stocktrend.web_sources import EiaIndustryAdapter, SecEdgarNewsAdapter


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
