"""Fail-closed live market-data sourcing and production input gates."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote, urlencode, urlsplit

from .config import ConfigBundle
from .contracts import SchemaRegistry
from .errors import ConfigurationError, ContractError, ProviderError, SafetyViolation
from .security import validate_public_https_url
from .util import (
    atomic_write_json,
    isoformat_utc,
    load_json,
    parse_datetime,
    sha256_json,
    utc_now,
)


class SourceAdapterError(ProviderError):
    """A source adapter failed without exposing raw provider content."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ReadOnlyMarketDataAdapter(ABC):
    adapter_id: str
    source_url: str
    license_class: str

    @abstractmethod
    def fetch_market_record(
        self,
        instrument: Mapping[str, Any],
        session_date: str,
    ) -> Dict[str, Any]:
        raise NotImplementedError


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore
        del req, fp, code, msg, headers, newurl
        raise SourceAdapterError("SOURCE_REDIRECT_REJECTED")


def _default_http_transport(
    request: urllib.request.Request,
    timeout_seconds: int,
) -> Any:
    opener = urllib.request.build_opener(_RejectRedirects())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            body = response.read(2_000_001)
    except SourceAdapterError:
        raise
    except urllib.error.HTTPError as exc:
        raise SourceAdapterError("SOURCE_HTTP_%d" % exc.code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SourceAdapterError("SOURCE_NETWORK_FAILED") from exc
    if len(body) > 2_000_000:
        raise SourceAdapterError("SOURCE_RESPONSE_TOO_LARGE")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceAdapterError("SOURCE_RESPONSE_NOT_JSON") from exc
    return value


class HttpJsonGatewayAdapter(ReadOnlyMarketDataAdapter):
    """Read-only adapter for an approved normalized HTTPS market-data gateway."""

    adapter_id = "http_json_gateway"

    def __init__(
        self,
        endpoint: str,
        allowed_host: str,
        token: str,
        license_class: str,
        timeout_seconds: int = 30,
        transport: Optional[
            Callable[[urllib.request.Request, int], Any]
        ] = None,
    ):
        validate_public_https_url(endpoint)
        parsed = urlsplit(endpoint)
        normalized_host = (parsed.hostname or "").lower().rstrip(".")
        if normalized_host != allowed_host.lower().rstrip("."):
            raise SafetyViolation("market-data endpoint host is not allowlisted")
        if parsed.query or parsed.fragment:
            raise SafetyViolation("market-data endpoint must not contain query or fragment")
        if not token:
            raise ConfigurationError("read-only market-data token is required")
        if timeout_seconds < 1 or timeout_seconds > 60:
            raise ConfigurationError("market-data timeout must be between 1 and 60 seconds")
        self.source_url = endpoint
        self.allowed_host = normalized_host
        self.token = token
        self.license_class = license_class
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _default_http_transport

    def fetch_market_record(
        self,
        instrument: Mapping[str, Any],
        session_date: str,
    ) -> Dict[str, Any]:
        query = urlencode(
            {
                "symbol": str(instrument["symbol"]),
                "session_date": session_date,
            }
        )
        request = urllib.request.Request(
            "%s?%s" % (self.source_url, query),
            headers={
                "Authorization": "Bearer %s" % self.token,
                "Accept": "application/json",
                "User-Agent": "stocktrend-source/1.0",
            },
            method="GET",
        )
        value = self.transport(request, self.timeout_seconds)
        if not isinstance(value, dict):
            raise SourceAdapterError("SOURCE_RESPONSE_NOT_OBJECT")
        return self._normalize_response(value, str(instrument["symbol"]))

    @staticmethod
    def _normalize_response(value: Dict[str, Any], symbol: str) -> Dict[str, Any]:
        if value.get("schema_version") != "1.0.0":
            raise SourceAdapterError("SOURCE_SCHEMA_VERSION_INVALID")
        if str(value.get("symbol", "")).upper() != symbol.upper():
            raise SourceAdapterError("SOURCE_SYMBOL_MISMATCH")
        quote = value.get("quote")
        metrics = value.get("bar_metrics")
        if not isinstance(quote, dict) or not isinstance(metrics, dict):
            raise SourceAdapterError("SOURCE_REQUIRED_FACTS_MISSING")
        normalized_quote = {
            "record_id": _required_text(quote, "record_id"),
            "observed_at": _required_timestamp(quote, "observed_at"),
            "price": _positive_number(quote, "price"),
            "bid": _positive_number(quote, "bid"),
            "ask": _positive_number(quote, "ask"),
        }
        if normalized_quote["ask"] < normalized_quote["bid"]:
            raise SourceAdapterError("SOURCE_QUOTE_CROSSED")
        normalized_metrics = {
            "record_id": _required_text(metrics, "record_id"),
            "observed_at": _required_timestamp(metrics, "observed_at"),
            "average_volume_20d": _positive_number(
                metrics, "average_volume_20d"
            ),
            "volume_ratio": _nonnegative_number(metrics, "volume_ratio"),
            "momentum_20d_pct": _number(metrics, "momentum_20d_pct"),
        }
        return {
            "quote": normalized_quote,
            "bar_metrics": normalized_metrics,
        }


class TiingoMarketDataAdapter(ReadOnlyMarketDataAdapter):
    """Read-only Tiingo adapter for live research snapshots and EOD history."""

    adapter_id = "tiingo_market_data"

    def __init__(
        self,
        base_url: str,
        allowed_host: str,
        token: str,
        license_class: str,
        timeout_seconds: int = 30,
        transport: Optional[Callable[[urllib.request.Request, int], Any]] = None,
    ):
        validate_public_https_url(base_url)
        parsed = urlsplit(base_url)
        normalized_host = (parsed.hostname or "").lower().rstrip(".")
        if normalized_host != allowed_host.lower().rstrip("."):
            raise SafetyViolation("Tiingo endpoint host is not allowlisted")
        if parsed.query or parsed.fragment:
            raise SafetyViolation("Tiingo base URL must not contain query or fragment")
        if not token:
            raise ConfigurationError("Tiingo read-only API token is required")
        if timeout_seconds < 1 or timeout_seconds > 60:
            raise ConfigurationError("market-data timeout must be between 1 and 60 seconds")
        self.source_url = base_url.rstrip("/")
        self.allowed_host = normalized_host
        self.token = token
        self.license_class = license_class
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _default_http_transport

    def fetch_market_record(
        self,
        instrument: Mapping[str, Any],
        session_date: str,
    ) -> Dict[str, Any]:
        symbol = str(instrument["symbol"]).upper()
        try:
            session = date.fromisoformat(session_date)
        except ValueError as exc:
            raise SourceAdapterError("SOURCE_SESSION_DATE_INVALID") from exc
        encoded_symbol = quote(symbol, safe="")
        snapshot = self._get_json(
            "/tiingo/equity/intraday/%s" % encoded_symbol,
            {},
        )
        history = self._get_json(
            "/tiingo/daily/%s/prices" % encoded_symbol,
            {
                "startDate": (session - timedelta(days=60)).isoformat(),
                "endDate": session.isoformat(),
                "resampleFreq": "daily",
            },
        )
        return self._normalize_response(snapshot, history, symbol, session)

    def _get_json(self, path: str, parameters: Mapping[str, str]) -> Any:
        query = urlencode(dict(parameters))
        url = "%s%s" % (self.source_url, path)
        if query:
            url = "%s?%s" % (url, query)
        parsed = urlsplit(url)
        if (parsed.hostname or "").lower().rstrip(".") != self.allowed_host:
            raise SafetyViolation("Tiingo request host is not allowlisted")
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": "Token %s" % self.token,
                "Accept": "application/json",
                "User-Agent": "stocktrend-source/1.0",
            },
            method="GET",
        )
        return self.transport(request, self.timeout_seconds)

    @staticmethod
    def _normalize_response(
        snapshot_value: Any,
        history_value: Any,
        symbol: str,
        session: date,
    ) -> Dict[str, Any]:
        snapshot = _single_tiingo_snapshot(snapshot_value, symbol)
        if not isinstance(history_value, list):
            raise SourceAdapterError("SOURCE_HISTORY_NOT_ARRAY")
        timestamp = _required_timestamp(snapshot, "timestamp")
        price = _positive_number(snapshot, "tngoLast")
        bid = _positive_number(snapshot, "lqBidPrice")
        ask = _positive_number(snapshot, "lqAskPrice")
        if ask < bid:
            raise SourceAdapterError("SOURCE_QUOTE_CROSSED")
        current_volume = _positive_number(snapshot, "volume")

        prior_bars: List[Tuple[date, float, float]] = []
        for item in history_value:
            if not isinstance(item, dict):
                raise SourceAdapterError("SOURCE_HISTORY_RECORD_INVALID")
            raw_date = item.get("date")
            if not isinstance(raw_date, str):
                raise SourceAdapterError("SOURCE_HISTORY_RECORD_INVALID")
            try:
                bar_date = date.fromisoformat(raw_date[:10])
            except ValueError as exc:
                raise SourceAdapterError("SOURCE_HISTORY_RECORD_INVALID") from exc
            if bar_date >= session:
                continue
            prior_bars.append(
                (
                    bar_date,
                    _positive_number(item, "adjClose"),
                    _positive_number(item, "adjVolume"),
                )
            )
        prior_bars.sort(key=lambda item: item[0], reverse=True)
        if len(prior_bars) < 20:
            raise SourceAdapterError("SOURCE_HISTORY_INSUFFICIENT")
        window = prior_bars[:20]
        average_volume = sum(item[2] for item in window) / 20.0
        comparison_close = window[-1][1]
        momentum = ((price / comparison_close) - 1.0) * 100.0
        volume_ratio = current_volume / average_volume
        history_record_date = window[0][0].isoformat()
        return {
            "quote": {
                "record_id": "tiingo:%s:snapshot:%s" % (symbol, timestamp),
                "observed_at": timestamp,
                "price": price,
                "bid": bid,
                "ask": ask,
            },
            "bar_metrics": {
                "record_id": "tiingo:%s:history:%s" % (symbol, history_record_date),
                "observed_at": timestamp,
                "average_volume_20d": average_volume,
                "volume_ratio": volume_ratio,
                "momentum_20d_pct": momentum,
            },
        }


def _single_tiingo_snapshot(value: Any, symbol: str) -> Mapping[str, Any]:
    if isinstance(value, dict):
        candidates = [value]
    elif isinstance(value, list):
        candidates = [item for item in value if isinstance(item, dict)]
    else:
        raise SourceAdapterError("SOURCE_RESPONSE_NOT_OBJECT")
    for item in candidates:
        if str(item.get("ticker", "")).upper() == symbol.upper():
            return item
    raise SourceAdapterError("SOURCE_SYMBOL_MISMATCH")


def _required_text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise SourceAdapterError("SOURCE_FIELD_INVALID_%s" % key.upper())
    return result.strip()


def _required_timestamp(value: Mapping[str, Any], key: str) -> str:
    result = _required_text(value, key)
    try:
        parse_datetime(result)
    except ValueError as exc:
        raise SourceAdapterError("SOURCE_FIELD_INVALID_%s" % key.upper()) from exc
    return result


def _number(value: Mapping[str, Any], key: str) -> float:
    raw = value.get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise SourceAdapterError("SOURCE_FIELD_INVALID_%s" % key.upper())
    result = float(raw)
    if result != result or result in (float("inf"), float("-inf")):
        raise SourceAdapterError("SOURCE_FIELD_INVALID_%s" % key.upper())
    return result


def _positive_number(value: Mapping[str, Any], key: str) -> float:
    result = _number(value, key)
    if result <= 0:
        raise SourceAdapterError("SOURCE_FIELD_INVALID_%s" % key.upper())
    return result


def _nonnegative_number(value: Mapping[str, Any], key: str) -> float:
    result = _number(value, key)
    if result < 0:
        raise SourceAdapterError("SOURCE_FIELD_INVALID_%s" % key.upper())
    return result


class SourceService:
    def __init__(
        self,
        root: Path,
        adapter: ReadOnlyMarketDataAdapter,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.root = root
        self.adapter = adapter
        self.clock = clock
        self.config = ConfigBundle.load(root)
        self.registry = SchemaRegistry(root / "schemas")
        self.heartbeat_path = root / "state" / "sourcing" / "heartbeat.json"

    def run(
        self,
        session_date: str,
        analysis_window: str = "close",
        output_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        try:
            session = date.fromisoformat(session_date)
        except ValueError as exc:
            raise ContractError("session_date must be YYYY-MM-DD") from exc
        if not analysis_window:
            raise ContractError("analysis_window is required")
        started = self.clock()
        if started.tzinfo is None:
            raise ConfigurationError("source clock must be timezone-aware")
        started_at = isoformat_utc(started)
        universe = self.config.universe
        heartbeat_id = "heartbeat_%s" % sha256_json(
            {
                "provider": self.adapter.adapter_id,
                "universe_version": universe["universe_version"],
                "started_at": started_at,
            }
        )[:24]
        heartbeat = {
            "schema_version": "1.0.0",
            "heartbeat_id": heartbeat_id,
            "provider": self.adapter.adapter_id,
            "universe_version": universe["universe_version"],
            "status": "running",
            "started_at": started_at,
            "completed_at": None,
            "snapshot_id": None,
            "snapshot_path": None,
            "snapshot_hash": None,
            "coverage_status": None,
            "error_code": None,
        }
        self._write_heartbeat(heartbeat)
        try:
            return self._collect(
                session,
                analysis_window,
                heartbeat,
                output_path,
            )
        except Exception as exc:
            heartbeat["status"] = "failed"
            heartbeat["completed_at"] = isoformat_utc(self.clock())
            heartbeat["error_code"] = getattr(exc, "code", "SOURCE_RUN_FAILED")
            self._write_heartbeat(heartbeat)
            raise

    def _collect(
        self,
        session: date,
        analysis_window: str,
        heartbeat: Dict[str, Any],
        output_path: Optional[Path],
    ) -> Dict[str, Any]:
        universe = self.config.universe
        reviewed_at = date.fromisoformat(str(universe["reviewed_at"]))
        review_deadline = reviewed_at + timedelta(
            days=int(universe["review_frequency_days"])
        )
        if session > review_deadline:
            raise SafetyViolation("universe review is stale for the requested session")
        required_buckets = list(universe["required_buckets"])
        instruments = [
            item
            for item in universe["instruments"]
            if _instrument_active_for_session(item, session)
        ]
        bucket_counts = {
            bucket: {
                "configured": 0,
                "attempted": 0,
                "valid": 0,
                "stale": 0,
                "missing": 0,
                "rejected": 0,
            }
            for bucket in required_buckets
        }
        for instrument in instruments:
            bucket_counts[instrument["bucket"]]["configured"] += 1
        records: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        errors: List[Dict[str, str]] = []
        for instrument in instruments:
            bucket = instrument["bucket"]
            bucket_counts[bucket]["attempted"] += 1
            try:
                record = self.adapter.fetch_market_record(
                    instrument,
                    session.isoformat(),
                )
                records.append((instrument, record))
            except SourceAdapterError as exc:
                counter = (
                    "missing"
                    if exc.code == "SOURCE_REQUIRED_FACTS_MISSING"
                    else "rejected"
                )
                bucket_counts[bucket][counter] += 1
                errors.append(
                    {
                        "symbol": instrument["symbol"],
                        "bucket": bucket,
                        "code": exc.code,
                    }
                )
        completed = self.clock()
        if completed.tzinfo is None:
            raise ConfigurationError("source clock must be timezone-aware")
        completed_at = isoformat_utc(completed)
        source_policy = self.config.sources["production"]
        observations: List[Dict[str, Any]] = []
        instrument_buckets: Dict[str, str] = {}
        for instrument, record in records:
            bucket = instrument["bucket"]
            freshness_code = _freshness_error(
                record,
                completed,
                int(source_policy["quote_max_age_seconds"]),
                int(source_policy["bar_metrics_max_age_seconds"]),
            )
            if freshness_code:
                bucket_counts[bucket]["stale"] += 1
                errors.append(
                    {
                        "symbol": instrument["symbol"],
                        "bucket": bucket,
                        "code": freshness_code,
                    }
                )
                continue
            observations.extend(
                _observations_for_record(
                    instrument,
                    record,
                    completed_at,
                    self.adapter,
                )
            )
            instrument_buckets[instrument["instrument_id"]] = bucket
            bucket_counts[bucket]["valid"] += 1
        minimum_per_bucket = int(universe["coverage"]["minimum_valid_per_bucket"])
        minimum_total = int(universe["coverage"]["minimum_valid_total"])
        valid_total = sum(item["valid"] for item in bucket_counts.values())
        configured_total = sum(item["configured"] for item in bucket_counts.values())
        ready = valid_total >= minimum_total and all(
            bucket_counts[bucket]["valid"] >= minimum_per_bucket
            for bucket in required_buckets
        )
        coverage_status = "ready" if ready else "incomplete"
        coverage = {
            "minimum_valid_per_bucket": minimum_per_bucket,
            "minimum_valid_total": minimum_total,
            "valid_total": valid_total,
            "configured_total": configured_total,
            "buckets": bucket_counts,
        }
        observations_hash = sha256_json(observations)
        universe_hash = sha256_json(universe)
        snapshot_id = "snapshot_%s" % sha256_json(
            {
                "provider": self.adapter.adapter_id,
                "universe_hash": universe_hash,
                "session_date": session.isoformat(),
                "as_of": completed_at,
                "observations_hash": observations_hash,
            }
        )[:24]
        freshness_deadline = isoformat_utc(
            completed + timedelta(seconds=int(source_policy["snapshot_max_age_seconds"]))
        )
        document = {
            "schema_version": "1.0.0",
            "session_date": session.isoformat(),
            "as_of": completed_at,
            "reference_venue": self.config.workflow["reference_market"],
            "analysis_window": analysis_window,
            "source_snapshot": {
                "snapshot_id": snapshot_id,
                "profile": "production",
                "provider": self.adapter.adapter_id,
                "universe_id": universe["universe_id"],
                "universe_version": universe["universe_version"],
                "universe_hash": universe_hash,
                "created_at": completed_at,
                "freshness_deadline": freshness_deadline,
                "observations_hash": observations_hash,
                "coverage_status": coverage_status,
                "coverage": coverage,
                "instrument_buckets": instrument_buckets,
                "errors": errors,
            },
            "observations": observations,
        }
        self.registry.validate("source_snapshot", document)
        snapshot_path = (
            self.root / "state" / "source_snapshots" / ("%s.json" % snapshot_id)
        )
        atomic_write_json(snapshot_path, document)
        if output_path is not None and output_path.resolve() != snapshot_path.resolve():
            atomic_write_json(output_path, document)
        snapshot_hash = sha256_json(document)
        heartbeat.update(
            {
                "status": "success" if ready else "incomplete",
                "completed_at": completed_at,
                "snapshot_id": snapshot_id,
                "snapshot_path": str(snapshot_path),
                "snapshot_hash": snapshot_hash,
                "coverage_status": coverage_status,
                "error_code": None,
            }
        )
        self._write_heartbeat(heartbeat)
        return {
            "document": document,
            "snapshot_path": str(snapshot_path),
            "output_path": str(output_path) if output_path is not None else None,
            "heartbeat": heartbeat,
        }

    def _write_heartbeat(self, heartbeat: Dict[str, Any]) -> None:
        self.registry.validate("source_heartbeat", heartbeat)
        atomic_write_json(self.heartbeat_path, heartbeat)


def _instrument_active_for_session(instrument: Mapping[str, Any], session: date) -> bool:
    if instrument.get("active") is not True:
        return False
    start = date.fromisoformat(str(instrument["effective_from"]))
    end_value = instrument.get("effective_to")
    end = date.fromisoformat(str(end_value)) if end_value else None
    return start <= session and (end is None or session <= end)


def _freshness_error(
    record: Mapping[str, Any],
    as_of: datetime,
    quote_max_age_seconds: int,
    metrics_max_age_seconds: int,
) -> Optional[str]:
    quote_time = parse_datetime(record["quote"]["observed_at"])
    metrics_time = parse_datetime(record["bar_metrics"]["observed_at"])
    if quote_time > as_of or metrics_time > as_of:
        return "SOURCE_FUTURE_OBSERVATION"
    if (as_of - quote_time).total_seconds() > quote_max_age_seconds:
        return "SOURCE_QUOTE_STALE"
    if (as_of - metrics_time).total_seconds() > metrics_max_age_seconds:
        return "SOURCE_BAR_METRICS_STALE"
    return None


def _observations_for_record(
    instrument: Mapping[str, Any],
    record: Mapping[str, Any],
    retrieved_at: str,
    adapter: ReadOnlyMarketDataAdapter,
) -> List[Dict[str, Any]]:
    source_common = {
        "provider": adapter.adapter_id,
        "url": adapter.source_url,
        "license_class": adapter.license_class,
        "adapter_version": "1.0.0",
    }
    trust = {
        "provenance_class": "licensed_market_data",
        "extraction_method": "deterministic",
        "corroboration_state": "primary",
    }
    quote = record["quote"]
    metrics = record["bar_metrics"]
    return [
        {
            "fact_type": "quote",
            "instrument_id": instrument["instrument_id"],
            "symbol": instrument["symbol"],
            "venue": instrument["venue"],
            "observed_at": quote["observed_at"],
            "retrieved_at": retrieved_at,
            "market_session": "regular",
            "value": {
                "price": quote["price"],
                "bid": quote["bid"],
                "ask": quote["ask"],
            },
            "unit": "price",
            "currency": "USD",
            "adjustment_status": "not_applicable",
            "source": dict(source_common, record_id=quote["record_id"]),
            "trust": dict(trust),
        },
        {
            "fact_type": "bar_metrics",
            "instrument_id": instrument["instrument_id"],
            "symbol": instrument["symbol"],
            "venue": instrument["venue"],
            "observed_at": metrics["observed_at"],
            "retrieved_at": retrieved_at,
            "market_session": "regular",
            "value": {
                "average_volume_20d": metrics["average_volume_20d"],
                "volume_ratio": metrics["volume_ratio"],
                "momentum_20d_pct": metrics["momentum_20d_pct"],
            },
            "unit": "mixed",
            "currency": "USD",
            "adjustment_status": "adjusted",
            "source": dict(source_common, record_id=metrics["record_id"]),
            "trust": dict(trust),
        },
    ]


def create_source_adapter(
    root: Path,
    adapter_name: Optional[str] = None,
) -> ReadOnlyMarketDataAdapter:
    config = ConfigBundle.load(root)
    production = config.sources["production"]
    approved_name = str(production["approved_adapter"])
    selected_name = adapter_name or approved_name
    if selected_name != approved_name:
        raise SafetyViolation("source adapter is not the approved production adapter")
    adapter = config.sources["adapters"].get(selected_name)
    if not isinstance(adapter, dict) or adapter.get("enabled") is not True:
        raise ConfigurationError("approved market-data adapter is disabled")
    token = os.environ.get(str(adapter["token_env"]), "").strip()
    if selected_name == "http_json_gateway":
        endpoint = os.environ.get(str(adapter["endpoint_env"]), "").strip()
        allowed_host = os.environ.get(str(adapter["allowed_host_env"]), "").strip()
        if not endpoint or not allowed_host:
            raise ConfigurationError(
                "market-data endpoint and allowlisted host are required"
            )
        return HttpJsonGatewayAdapter(
            endpoint=endpoint,
            allowed_host=allowed_host,
            token=token,
            license_class=str(adapter["license_class"]),
            timeout_seconds=int(adapter["timeout_seconds"]),
        )
    if selected_name == "tiingo_market_data":
        return TiingoMarketDataAdapter(
            base_url=str(adapter["base_url"]),
            allowed_host=str(adapter["allowed_host"]),
            token=token,
            license_class=str(adapter["license_class"]),
            timeout_seconds=int(adapter["timeout_seconds"]),
        )
    raise ConfigurationError("unsupported source adapter: %s" % selected_name)


def source_status(
    root: Path,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    config = ConfigBundle.load(root)
    registry = SchemaRegistry(root / "schemas")
    heartbeat_path = root / "state" / "sourcing" / "heartbeat.json"
    if not heartbeat_path.exists():
        return {
            "heartbeat_present": False,
            "analysis_allowed": False,
            "execution_source_ready": False,
            "stale": True,
            "blocking_reasons": ["SOURCE_HEARTBEAT_MISSING"],
            "degraded_reasons": [],
        }
    heartbeat = load_json(heartbeat_path)
    registry.validate("source_heartbeat", heartbeat)
    current = now or utc_now()
    completed_at = heartbeat.get("completed_at")
    stale = completed_at is None
    if completed_at is not None:
        age = (current - parse_datetime(completed_at)).total_seconds()
        stale = age < 0 or age > int(
            config.sources["production"]["heartbeat_max_age_seconds"]
        )
    blocking: List[str] = []
    if stale:
        blocking.append("SOURCE_HEARTBEAT_STALE")
    if heartbeat["status"] in ("running", "failed"):
        blocking.append("SOURCE_HEARTBEAT_%s" % heartbeat["status"].upper())
    degraded = (
        ["SOURCE_COVERAGE_INCOMPLETE"]
        if heartbeat["status"] == "incomplete"
        else []
    )
    analysis_allowed = not blocking and heartbeat["status"] in (
        "success",
        "incomplete",
    )
    return {
        "heartbeat_present": True,
        "analysis_allowed": analysis_allowed,
        "execution_source_ready": analysis_allowed
        and heartbeat["status"] == "success",
        "stale": stale,
        "blocking_reasons": sorted(set(blocking)),
        "degraded_reasons": degraded,
        "heartbeat": heartbeat,
    }


def validate_run_input(
    root: Path,
    document: Dict[str, Any],
    profile: str,
    now: Optional[datetime] = None,
) -> List[str]:
    if profile == "test":
        return ["NON_PRODUCTION_INPUT"]
    if profile != "production":
        raise ConfigurationError("unknown input profile: %s" % profile)
    for observation in document.get("observations", []):
        trust = observation.get("trust", {})
        if trust.get("extraction_method") == "fixture" or trust.get(
            "provenance_class"
        ) == "fixture":
            raise SafetyViolation("fixture provenance is prohibited in production")
    config = ConfigBundle.load(root)
    registry = SchemaRegistry(root / "schemas")
    registry.validate("source_snapshot", document)
    metadata = document["source_snapshot"]
    approved_name = str(config.sources["production"]["approved_adapter"])
    approved = config.sources["adapters"].get(approved_name, {})
    if approved.get("enabled") is not True:
        raise ConfigurationError("approved market-data adapter is disabled")
    if metadata["provider"] != approved_name:
        raise SafetyViolation("snapshot provider is not approved")
    universe = config.universe
    if metadata["universe_id"] != universe["universe_id"]:
        raise ContractError("snapshot universe id does not match configuration")
    if metadata["universe_version"] != universe["universe_version"]:
        raise ContractError("snapshot universe version does not match configuration")
    if metadata["universe_hash"] != sha256_json(universe):
        raise ContractError("snapshot universe hash does not match configuration")
    if metadata["observations_hash"] != sha256_json(document["observations"]):
        raise ContractError("snapshot observations hash mismatch")
    if not document["observations"]:
        raise ContractError("production snapshot has no valid observations")
    for observation in document["observations"]:
        source = observation.get("source", {})
        trust = observation.get("trust", {})
        if source.get("provider") != approved_name:
            raise SafetyViolation("observation provider does not match snapshot")
        if trust.get("extraction_method") == "fixture" or trust.get(
            "provenance_class"
        ) == "fixture":
            raise SafetyViolation("fixture provenance is prohibited in production")
    current = now or utc_now()
    if current > parse_datetime(metadata["freshness_deadline"]):
        raise SafetyViolation("source snapshot is stale")
    status = source_status(root, now=current)
    if not status["analysis_allowed"]:
        raise SafetyViolation(
            "source heartbeat blocks analysis: %s"
            % ", ".join(status["blocking_reasons"])
        )
    heartbeat = status["heartbeat"]
    if heartbeat["snapshot_id"] != metadata["snapshot_id"]:
        raise ContractError("source heartbeat snapshot id mismatch")
    if heartbeat["snapshot_hash"] != sha256_json(document):
        raise ContractError("source heartbeat snapshot hash mismatch")
    snapshot_path = Path(str(heartbeat["snapshot_path"])).resolve()
    snapshot_root = (root / "state" / "source_snapshots").resolve()
    try:
        snapshot_path.relative_to(snapshot_root)
    except ValueError as exc:
        raise SafetyViolation("source snapshot path escapes operational state") from exc
    if not snapshot_path.exists() or sha256_json(load_json(snapshot_path)) != sha256_json(
        document
    ):
        raise ContractError("durable source snapshot does not match input")
    degraded = list(status["degraded_reasons"])
    if metadata["coverage_status"] == "incomplete" and (
        "SOURCE_COVERAGE_INCOMPLETE" not in degraded
    ):
        degraded.append("SOURCE_COVERAGE_INCOMPLETE")
    return sorted(set(degraded))
