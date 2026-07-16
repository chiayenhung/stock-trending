"""Deterministic public-web enrichers for untrusted research content."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlencode, urljoin, urlsplit

from .errors import ConfigurationError, ProviderError, SafetyViolation
from .security import validate_public_https_url


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
