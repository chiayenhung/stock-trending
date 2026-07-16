"""Deterministic public-web enrichers for untrusted research content."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlencode, urljoin, urlsplit

from .contracts import SchemaRegistry
from .errors import (
    ConfigurationError,
    ContractError,
    ProviderError,
    SafetyViolation,
)
from .security import validate_public_https_url
from .util import load_json, parse_datetime


class WebSourceError(ProviderError):
    """A public-web source failed without persisting its raw response."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore
        del req, fp, code, msg, headers, newurl
        raise WebSourceError("WEB_SOURCE_REDIRECT_REJECTED")


def _default_html_transport(
    request: urllib.request.Request,
    timeout_seconds: int,
) -> str:
    opener = urllib.request.build_opener(_RejectRedirects())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            body = response.read(2_000_001)
    except WebSourceError:
        raise
    except urllib.error.HTTPError as exc:
        raise WebSourceError("WEB_SOURCE_HTTP_%d" % exc.code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise WebSourceError("WEB_SOURCE_NETWORK_FAILED") from exc
    if len(body) > 2_000_000:
        raise WebSourceError("WEB_SOURCE_RESPONSE_TOO_LARGE")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WebSourceError("WEB_SOURCE_RESPONSE_NOT_UTF8") from exc


HtmlTransport = Callable[[urllib.request.Request, int], str]


@dataclass(frozen=True)
class EnrichmentFetchResult:
    observations: List[Dict[str, Any]]
    attempted: int
    failed: int
    error_code: Optional[str] = None


class ReadOnlyEnrichmentAdapter(ABC):
    source_kind: str
    adapter_id: str
    source_url: str
    license_class: str
    extraction_method = "deterministic"
    model: Optional[str] = None

    @abstractmethod
    def fetch_observations(
        self,
        instruments: Sequence[Mapping[str, Any]],
        session_date: str,
        retrieved_at: str,
    ) -> EnrichmentFetchResult:
        raise NotImplementedError


def _normalize_text(parts: Sequence[str], maximum: int = 1200) -> str:
    value = " ".join(" ".join(parts).split())
    return value[:maximum]


class _SecFilingsParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.rows: List[Dict[str, str]] = []
        self._in_row = False
        self._in_cell = False
        self._cell_parts: List[str] = []
        self._cells: List[str] = []
        self._document_url: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self._in_row = True
            self._cells = []
            self._document_url = None
        elif tag == "td" and self._in_row:
            self._in_cell = True
            self._cell_parts = []
        elif tag == "a" and self._in_cell:
            href = str(attributes.get("href", ""))
            if "/Archives/edgar/data/" in href and href.endswith("-index.htm"):
                self._document_url = urljoin(self.base_url, href)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_cell:
            self._cells.append(_normalize_text(self._cell_parts))
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            self._finish_row()
            self._in_row = False

    def _finish_row(self) -> None:
        if len(self._cells) < 4 or self._document_url is None:
            return
        accession_match = re.search(r"Acc-no:\s*([0-9-]+)", self._cells[2])
        if accession_match is None:
            return
        self.rows.append(
            {
                "form_type": self._cells[0],
                "description": self._cells[2],
                "filing_date": self._cells[3],
                "accession_number": accession_match.group(1),
                "url": self._document_url,
            }
        )


class SecEdgarNewsAdapter(ReadOnlyEnrichmentAdapter):
    source_kind = "news"
    adapter_id = "sec_edgar_html"

    def __init__(
        self,
        page_url: str,
        allowed_host: str,
        license_class: str,
        user_agent: str,
        timeout_seconds: int = 20,
        max_age_days: int = 90,
        max_items_per_instrument: int = 2,
        form_types: Optional[Sequence[str]] = None,
        transport: Optional[HtmlTransport] = None,
    ):
        _validate_web_adapter(page_url, allowed_host, timeout_seconds)
        if not user_agent.strip():
            raise ConfigurationError("SEC public-web user agent is required")
        self.source_url = page_url
        self.allowed_host = allowed_host.lower().rstrip(".")
        self.license_class = license_class
        self.user_agent = user_agent.strip()
        self.timeout_seconds = timeout_seconds
        self.max_age_days = max_age_days
        self.max_items_per_instrument = max_items_per_instrument
        self.form_types = set(form_types or ("8-K", "10-K", "10-Q", "6-K", "20-F"))
        self.transport = transport or _default_html_transport

    def fetch_observations(
        self,
        instruments: Sequence[Mapping[str, Any]],
        session_date: str,
        retrieved_at: str,
    ) -> EnrichmentFetchResult:
        session = date.fromisoformat(session_date)
        observations: List[Dict[str, Any]] = []
        failed = 0
        error_codes = []
        for instrument in instruments:
            try:
                filings = self._fetch_filings(str(instrument["symbol"]))
            except WebSourceError as exc:
                failed += 1
                error_codes.append(exc.code)
                continue
            selected = []
            for filing in filings:
                try:
                    filing_date = date.fromisoformat(filing["filing_date"])
                except ValueError:
                    continue
                if filing["form_type"] not in self.form_types:
                    continue
                if filing_date >= session:
                    continue
                if (session - filing_date).days > self.max_age_days:
                    continue
                selected.append((filing_date, filing))
            selected.sort(
                key=lambda item: (item[0], item[1]["accession_number"]),
                reverse=True,
            )
            for filing_date, filing in selected[: self.max_items_per_instrument]:
                observations.append(
                    self._observation(instrument, filing, filing_date, retrieved_at)
                )
        error_code = None
        if failed:
            unique_errors = set(error_codes)
            error_code = (
                error_codes[0]
                if failed == len(instruments) and len(unique_errors) == 1
                else "NEWS_SOURCE_PARTIAL"
            )
        return EnrichmentFetchResult(
            observations=observations,
            attempted=len(instruments),
            failed=failed,
            error_code=error_code,
        )

    def _fetch_filings(self, symbol: str) -> List[Dict[str, str]]:
        query = urlencode(
            {
                "action": "getcompany",
                "CIK": symbol,
                "owner": "exclude",
                "count": "10",
            }
        )
        url = "%s?%s" % (self.source_url, query)
        _validate_request_host(url, self.allowed_host)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": self.user_agent,
            },
            method="GET",
        )
        parser = _SecFilingsParser(self.source_url)
        parser.feed(self.transport(request, self.timeout_seconds))
        parser.close()
        return parser.rows

    def _observation(
        self,
        instrument: Mapping[str, Any],
        filing: Mapping[str, str],
        filing_date: date,
        retrieved_at: str,
    ) -> Dict[str, Any]:
        return {
            "fact_type": "news",
            "instrument_id": instrument["instrument_id"],
            "symbol": instrument["symbol"],
            "venue": instrument["venue"],
            "observed_at": "%sT00:00:00Z" % filing_date.isoformat(),
            "retrieved_at": retrieved_at,
            "market_session": "non_market",
            "value": {
                "headline": "%s filing: %s"
                % (filing["form_type"], filing["description"]),
                "form_type": filing["form_type"],
                "description": filing["description"],
                "filing_date": filing["filing_date"],
                "timestamp_precision": "date",
            },
            "unit": None,
            "currency": None,
            "adjustment_status": "not_applicable",
            "source": {
                "provider": self.adapter_id,
                "record_id": filing["accession_number"],
                "url": filing["url"],
                "license_class": self.license_class,
                "adapter_version": "1.0.0",
            },
            "trust": {
                "provenance_class": "official_regulatory_filing",
                "extraction_method": "deterministic",
                "corroboration_state": "primary",
            },
        }


class _EiaArticlesParser(HTMLParser):
    _DATE_PATTERN = re.compile(
        r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}, \d{4}$"
    )

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.records: List[Dict[str, str]] = []
        self._pending_date: Optional[str] = None
        self._in_heading = False
        self._heading_href: Optional[str] = None
        self._heading_parts: List[str] = []
        self._in_paragraph = False
        self._paragraph_parts: List[str] = []
        self._current: Optional[Dict[str, str]] = None

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        attributes = dict(attrs)
        if tag == "h1":
            self._finish_current()
            self._in_heading = True
            self._heading_href = None
            self._heading_parts = []
        elif tag == "a" and self._in_heading:
            href = str(attributes.get("href", ""))
            if "detail.php?id=" in href:
                self._heading_href = urljoin(self.base_url, href)
        elif tag == "p" and self._current is not None:
            self._in_paragraph = True
            self._paragraph_parts = []

    def handle_data(self, data: str) -> None:
        stripped = " ".join(data.split())
        if self._DATE_PATTERN.match(stripped):
            self._pending_date = stripped
        if self._in_heading:
            self._heading_parts.append(data)
        if self._in_paragraph:
            self._paragraph_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._in_heading:
            title = _normalize_text(self._heading_parts, maximum=500)
            if self._heading_href and self._pending_date and title != "Today in Energy":
                self._current = {
                    "title": title,
                    "published_date": self._pending_date,
                    "url": self._heading_href,
                    "summary": "",
                }
            self._in_heading = False
        elif tag == "p" and self._in_paragraph:
            paragraph = _normalize_text(self._paragraph_parts)
            if (
                self._current is not None
                and len(paragraph) > len(self._current["summary"])
                and not paragraph.startswith("Tags:")
            ):
                self._current["summary"] = paragraph
            self._in_paragraph = False

    def close(self) -> None:
        super().close()
        self._finish_current()

    def _finish_current(self) -> None:
        if self._current is not None:
            self.records.append(self._current)
            self._current = None


class EiaIndustryAdapter(ReadOnlyEnrichmentAdapter):
    source_kind = "industry"
    adapter_id = "eia_today_in_energy_html"

    def __init__(
        self,
        page_url: str,
        allowed_host: str,
        license_class: str,
        timeout_seconds: int = 20,
        max_age_days: int = 60,
        max_items: int = 3,
        applicable_buckets: Optional[Sequence[str]] = None,
        relevance_keywords: Optional[Sequence[str]] = None,
        transport: Optional[HtmlTransport] = None,
    ):
        _validate_web_adapter(page_url, allowed_host, timeout_seconds)
        self.source_url = page_url
        self.allowed_host = allowed_host.lower().rstrip(".")
        self.license_class = license_class
        self.timeout_seconds = timeout_seconds
        self.max_age_days = max_age_days
        self.max_items = max_items
        self.applicable_buckets = set(
            applicable_buckets or ("power_infrastructure",)
        )
        self.relevance_keywords = tuple(
            item.lower()
            for item in (
                relevance_keywords
                or (
                    "electricity",
                    "electric grid",
                    "generation",
                    "generating capacity",
                    "nuclear",
                    "solar",
                    "wind",
                    "data center",
                    "power demand",
                )
            )
        )
        self.transport = transport or _default_html_transport

    def fetch_observations(
        self,
        instruments: Sequence[Mapping[str, Any]],
        session_date: str,
        retrieved_at: str,
    ) -> EnrichmentFetchResult:
        session = date.fromisoformat(session_date)
        request = urllib.request.Request(
            self.source_url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "stocktrend-public-web/1.0",
            },
            method="GET",
        )
        try:
            _validate_request_host(self.source_url, self.allowed_host)
            html = self.transport(request, self.timeout_seconds)
        except WebSourceError as exc:
            return EnrichmentFetchResult([], 1, 1, exc.code)
        parser = _EiaArticlesParser(self.source_url)
        parser.feed(html)
        parser.close()
        selected = []
        for article in parser.records:
            try:
                published = date.fromisoformat(
                    _month_name_date_to_iso(article["published_date"])
                )
            except ValueError:
                continue
            if (
                published >= session
                or (session - published).days > self.max_age_days
            ):
                continue
            evidence = "%s %s" % (article["title"], article["summary"])
            if not any(
                keyword in evidence.lower()
                for keyword in self.relevance_keywords
            ):
                continue
            selected.append((published, article))
        selected.sort(key=lambda item: (item[0], item[1]["url"]), reverse=True)
        observations = []
        scoped_instruments = [
            item
            for item in instruments
            if str(item.get("bucket")) in self.applicable_buckets
        ]
        for published, article in selected[: self.max_items]:
            for instrument in scoped_instruments:
                observations.append(
                    self._observation(instrument, article, published, retrieved_at)
                )
        return EnrichmentFetchResult(observations, 1, 0, None)

    def _observation(
        self,
        instrument: Mapping[str, Any],
        article: Mapping[str, str],
        published: date,
        retrieved_at: str,
    ) -> Dict[str, Any]:
        record_id = (
            urlsplit(article["url"]).query.replace("id=", "")
            or article["url"]
        )
        return {
            "fact_type": "industry_datapoint",
            "instrument_id": instrument["instrument_id"],
            "symbol": instrument["symbol"],
            "venue": instrument["venue"],
            "observed_at": "%sT00:00:00Z" % published.isoformat(),
            "retrieved_at": retrieved_at,
            "market_session": "non_market",
            "value": {
                "headline": article["title"],
                "summary": article["summary"],
                "published_date": published.isoformat(),
                "industry_bucket": instrument["bucket"],
                "timestamp_precision": "date",
            },
            "unit": None,
            "currency": None,
            "adjustment_status": "not_applicable",
            "source": {
                "provider": self.adapter_id,
                "record_id": "eia:%s" % record_id,
                "url": article["url"],
                "license_class": self.license_class,
                "adapter_version": "1.0.0",
            },
            "trust": {
                "provenance_class": "official_industry_publication",
                "extraction_method": "deterministic",
                "corroboration_state": "primary",
            },
        }


class XBrowserSnapshotAdapter(ReadOnlyEnrichmentAdapter):
    """Read a bounded X capture produced by the authenticated host browser."""

    source_kind = "social"
    adapter_id = "x_browser_snapshot"

    def __init__(
        self,
        profile_url: str,
        account: str,
        allowed_host: str,
        allowlist: Sequence[str],
        snapshot_path: Path,
        schema_dir: Path,
        license_class: str,
        snapshot_max_age_seconds: int = 1800,
        lookback_days: int = 5,
        max_items_per_instrument: int = 5,
    ):
        validate_public_https_url(profile_url)
        parsed = urlsplit(profile_url)
        normalized_host = (parsed.hostname or "").lower().rstrip(".")
        normalized_allowlist = {
            str(item).lower().rstrip(".") for item in allowlist
        }
        if normalized_host != allowed_host.lower().rstrip("."):
            raise SafetyViolation("X profile host is not the configured host")
        if normalized_host not in normalized_allowlist:
            raise SafetyViolation("X profile host is not allowlisted")
        if parsed.query or parsed.fragment:
            raise SafetyViolation("X profile URL must not contain query or fragment")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", account):
            raise ConfigurationError("X account is invalid")
        if parsed.path.rstrip("/") != "/%s" % account:
            raise SafetyViolation("X profile URL does not match the configured account")
        if snapshot_path.parent.name != "social" or (
            snapshot_path.parent.parent.name != "state"
        ):
            raise SafetyViolation(
                "social browser snapshot must stay under state/social"
            )
        if snapshot_max_age_seconds < 1 or snapshot_max_age_seconds > 3600:
            raise ConfigurationError(
                "social browser snapshot age must be between 1 and 3600 seconds"
            )
        if lookback_days != 5:
            raise ConfigurationError("social browser lookback must be five days")
        if max_items_per_instrument < 1 or max_items_per_instrument > 20:
            raise ConfigurationError(
                "social item limit must be between 1 and 20"
            )
        self.source_url = profile_url.rstrip("/")
        self.account = account
        self.allowed_host = normalized_host
        self.snapshot_path = snapshot_path.resolve()
        self.registry = SchemaRegistry(schema_dir)
        self.license_class = license_class
        self.snapshot_max_age_seconds = snapshot_max_age_seconds
        self.lookback_days = lookback_days
        self.max_items_per_instrument = max_items_per_instrument

    def fetch_observations(
        self,
        instruments: Sequence[Mapping[str, Any]],
        session_date: str,
        retrieved_at: str,
    ) -> EnrichmentFetchResult:
        del session_date
        try:
            snapshot = self._load_snapshot(retrieved_at)
            observations = self._observations(
                snapshot,
                instruments,
                retrieved_at,
            )
        except WebSourceError as exc:
            return EnrichmentFetchResult([], 1, 1, exc.code)
        return EnrichmentFetchResult(observations, 1, 0, None)

    def _load_snapshot(self, retrieved_at: str) -> Dict[str, Any]:
        if not self.snapshot_path.exists():
            raise WebSourceError("SOCIAL_BROWSER_SNAPSHOT_MISSING")
        try:
            value = load_json(self.snapshot_path)
            self.registry.validate("social_browser_snapshot", value)
            self._validate_snapshot(value, retrieved_at)
        except WebSourceError:
            raise
        except (
            ContractError,
            OSError,
            SafetyViolation,
            TypeError,
            ValueError,
        ) as exc:
            raise WebSourceError("SOCIAL_BROWSER_SNAPSHOT_INVALID") from exc
        return value

    def _validate_snapshot(
        self,
        snapshot: Mapping[str, Any],
        retrieved_at: str,
    ) -> None:
        if snapshot.get("account") != self.account:
            raise WebSourceError("SOCIAL_BROWSER_ACCOUNT_MISMATCH")
        if str(snapshot.get("profile_url", "")).rstrip("/") != self.source_url:
            raise WebSourceError("SOCIAL_BROWSER_PROFILE_MISMATCH")
        retrieved = parse_datetime(retrieved_at)
        captured = parse_datetime(str(snapshot["captured_at"]))
        window_start = parse_datetime(str(snapshot["window_start"]))
        window_end = parse_datetime(str(snapshot["window_end"]))
        if captured > retrieved:
            raise WebSourceError("SOCIAL_BROWSER_SNAPSHOT_FUTURE")
        snapshot_age = (retrieved - captured).total_seconds()
        if snapshot_age > self.snapshot_max_age_seconds:
            raise WebSourceError("SOCIAL_BROWSER_SNAPSHOT_STALE")
        if window_end != captured:
            raise WebSourceError("SOCIAL_BROWSER_WINDOW_INVALID")
        expected_seconds = self.lookback_days * 24 * 60 * 60
        if (window_end - window_start).total_seconds() != expected_seconds:
            raise WebSourceError("SOCIAL_BROWSER_WINDOW_INVALID")
        seen = set()
        for post in snapshot["posts"]:
            post_id = str(post["post_id"])
            if post_id in seen:
                raise WebSourceError("SOCIAL_BROWSER_POST_DUPLICATE")
            seen.add(post_id)
            post_url = str(post["url"])
            validate_public_https_url(post_url)
            parsed = urlsplit(post_url)
            if (parsed.hostname or "").lower().rstrip(".") != self.allowed_host:
                raise WebSourceError("SOCIAL_BROWSER_POST_HOST_INVALID")
            if parsed.query or parsed.fragment:
                raise WebSourceError("SOCIAL_BROWSER_POST_URL_INVALID")
            expected_path = "/%s/status/%s" % (self.account, post_id)
            if parsed.path.rstrip("/") != expected_path:
                raise WebSourceError("SOCIAL_BROWSER_POST_URL_INVALID")
            posted_at = parse_datetime(str(post["posted_at"]))
            if not window_start <= posted_at <= window_end:
                raise WebSourceError("SOCIAL_BROWSER_POST_OUT_OF_WINDOW")
            if posted_at > retrieved:
                raise WebSourceError("SOCIAL_BROWSER_POST_FUTURE")

    def _observations(
        self,
        snapshot: Mapping[str, Any],
        instruments: Sequence[Mapping[str, Any]],
        retrieved_at: str,
    ) -> List[Dict[str, Any]]:
        instrument_by_symbol = {
            str(item["symbol"]).upper(): item for item in instruments
        }
        counts = {symbol: 0 for symbol in instrument_by_symbol}
        rows = sorted(
            snapshot["posts"],
            key=lambda item: (parse_datetime(item["posted_at"]), item["post_id"]),
            reverse=True,
        )
        observations: List[Dict[str, Any]] = []
        for post in rows:
            text = _normalize_text([str(post["text"])], maximum=1200)
            cashtags = {
                item.upper()
                for item in re.findall(
                    r"(?<![A-Za-z0-9])\$([A-Za-z][A-Za-z0-9.-]{0,15})(?![A-Za-z0-9])",
                    text,
                )
            }
            for symbol in sorted(cashtags & set(instrument_by_symbol)):
                if counts[symbol] >= self.max_items_per_instrument:
                    continue
                observations.append(
                    self._observation(
                        instrument_by_symbol[symbol],
                        post,
                        text,
                        retrieved_at,
                    )
                )
                counts[symbol] += 1
        return observations

    def _observation(
        self,
        instrument: Mapping[str, Any],
        post: Mapping[str, Any],
        text: str,
        retrieved_at: str,
    ) -> Dict[str, Any]:
        return {
            "fact_type": "social_post",
            "instrument_id": instrument["instrument_id"],
            "symbol": instrument["symbol"],
            "venue": instrument["venue"],
            "observed_at": post["posted_at"],
            "retrieved_at": retrieved_at,
            "market_session": "non_market",
            "value": {
                "text_excerpt": text,
                "account": "@%s" % self.account,
                "post_id": post["post_id"],
                "matched_cashtag": "$%s" % instrument["symbol"],
                "capture_method": "browser",
            },
            "unit": None,
            "currency": None,
            "adjustment_status": "not_applicable",
            "source": {
                "provider": self.adapter_id,
                "record_id": post["post_id"],
                "url": post["url"],
                "license_class": self.license_class,
                "adapter_version": "1.0.0",
            },
            "trust": {
                "provenance_class": "public_social_browser",
                "extraction_method": "deterministic",
                "corroboration_state": "uncorroborated",
            },
        }


def _month_name_date_to_iso(value: str) -> str:
    from datetime import datetime

    return datetime.strptime(value, "%b %d, %Y").date().isoformat()


def _validate_web_adapter(
    page_url: str,
    allowed_host: str,
    timeout_seconds: int,
) -> None:
    validate_public_https_url(page_url)
    parsed = urlsplit(page_url)
    if (parsed.hostname or "").lower().rstrip(
        "."
    ) != allowed_host.lower().rstrip("."):
        raise SafetyViolation("public-web source host is not allowlisted")
    if parsed.query or parsed.fragment:
        raise SafetyViolation("public-web source URL must not contain query or fragment")
    if timeout_seconds < 1 or timeout_seconds > 60:
        raise ConfigurationError("public-web timeout must be between 1 and 60 seconds")


def _validate_request_host(url: str, allowed_host: str) -> None:
    parsed = urlsplit(url)
    if (parsed.hostname or "").lower().rstrip(".") != allowed_host:
        raise SafetyViolation("public-web request host is not allowlisted")
