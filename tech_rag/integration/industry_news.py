"""Bounded, generic Yahoo industry discovery; search metadata is never evidence.

``calls`` counts SDK invocations. ``network_calls`` counts the installed SDK's
actual HTTP GET attempts, including its authentication traffic; cached SDK
responses therefore do not falsely claim a new network execution.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time

from .news_body import fetch_article, parse_stamp, safe_url

_SEARCH_LOCK = threading.Lock()
_SEARCH_SESSION = None


def _fetch_yahoo(query, count, transport_limit=None, before_request=None):
    import yfinance as yf
    from curl_cffi import requests as curl_requests

    class ObservedSession(curl_requests.Session):
        def __init__(self):
            super().__init__(impersonate="chrome")
            self.network_calls = 0

        def get(self, *args, **kwargs):
            ceiling = getattr(self, 'request_ceiling', None)
            if ceiling is not None and self.network_calls >= ceiling:
                raise RuntimeError('Yahoo transport request limit')
            callback=getattr(self,'before_request',None)
            if callback:callback()
            self.network_calls += 1
            return super().get(*args, **kwargs)

    # Keep the session alive: the installed SDK shares its data session.
    global _SEARCH_SESSION
    with _SEARCH_LOCK:
        if _SEARCH_SESSION is None:
            _SEARCH_SESSION = ObservedSession()
        before = _SEARCH_SESSION.network_calls
        _SEARCH_SESSION.before_request=before_request
        _SEARCH_SESSION.request_ceiling = before + transport_limit if transport_limit is not None else None
        try:
            result = yf.Search(query, max_results=0, news_count=count, lists_count=0,
                               include_cb=False, include_nav_links=False,
                               include_research=False, recommended=0,
                               timeout=10, raise_errors=True,
                               enable_fuzzy_query=False, session=_SEARCH_SESSION)
            return {"news": result.news or [],
                    "network_calls": _SEARCH_SESSION.network_calls - before}
        except Exception as exc:
            return {"news": [], "error_type": type(exc).__name__,
                    "network_calls": _SEARCH_SESSION.network_calls - before}
        finally:
            _SEARCH_SESSION.request_ceiling = None
            _SEARCH_SESSION.before_request = None


def _window_stamp(value, *, end=False):
    if isinstance(value, str) and len(value) == 10:
        return parse_stamp(value + ("T23:59:59.999999+00:00" if end else "T00:00:00+00:00"))
    return parse_stamp(value)


def search_yahoo(task: dict, as_of: str, *, historical=False, max_results=3,
                 cache_dir: Path | None = None, transport_limit=None,before_request=None) -> dict:
    """Call ``yfinance.Search`` with the task's industry phrase, without a ticker.

    Yahoo relevance ranking is not a comprehensive time-filtered news archive.
    Its timestamps are provider metadata until the original HTML is verified.
    ``cache_dir`` writes an audit record only and is never a fallback provider.
    """
    started = time.perf_counter()
    cutoff = parse_stamp(as_of)
    if cutoff is None:
        raise ValueError("as_of requires an ISO timestamp with timezone")
    if not isinstance(task, dict):
        raise ValueError("task must be a dictionary")
    if type(max_results) is not int or not 1 <= max_results <= 10:
        raise ValueError("max_results must be 1..10")
    if transport_limit is not None and (type(transport_limit) is not int or not 1 <= transport_limit <= 20):
        raise ValueError('transport_limit must be 1..20')
    query = task.get("query_en") or task.get("query_zh") or ""
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError("industry query must be 1..500 characters")
    query = query.strip()
    window = task.get("time_window") or {}
    if not isinstance(window, dict):
        raise ValueError("time_window must be a dictionary")
    begin = _window_stamp(window.get("start")) if window.get("start") else None
    end = _window_stamp(window.get("end"), end=True) if window.get("end") else cutoff
    if (window.get("start") and begin is None) or end is None or (begin and begin > end):
        raise ValueError("invalid time_window")
    end = min(end, cutoff)
    collected = datetime.now(timezone.utc).isoformat()
    result = {"provider": "Yahoo Finance via installed yfinance.Search",
              "status": "unsupported_historical" if historical else "no_candidates",
              "task_id": task.get("task_id"), "query": query,
              "query_en": task.get("query_en"), "query_zh": task.get("query_zh"),
              "as_of": cutoff.isoformat(), "time_window": window,
              "items": [], "calls": 0, "network_calls": 0, "seconds": 0.0,
              "real_network_executed": False, "collected_at": collected,
              "body_acquired": False, "source_verified": False,
              "historical_eligible": False, "paid_api_used": False,
              "coverage": "Yahoo relevance-ranked sample; not exhaustive industry coverage.",
              "unknowns": [], "errors": []}
    if historical:
        result["unknowns"].append("no_point_in_time_search_provider")
    else:
        result["calls"] = 1
        try:
            fetched = (_fetch_yahoo(query,max_results,transport_limit=transport_limit,before_request=before_request) if before_request else
                       _fetch_yahoo(query, max_results, transport_limit=transport_limit)
                       if transport_limit is not None else _fetch_yahoo(query, max_results))
        except Exception as exc:
            fetched = {"news": [], "network_calls": 0, "error_type": type(exc).__name__}
        result["network_calls"] = fetched.get("network_calls", 0)
        result["real_network_executed"] = result["network_calls"] > 0
        if fetched.get("error_type"):
            result["status"] = "provider_unavailable"
            result["errors"].append({"code": "provider_unavailable", "type": fetched["error_type"]})
        else:
            seen = set()
            excluded = 0
            articles = fetched.get("news")
            if not isinstance(articles, list):
                result["status"] = "provider_unavailable"
                result["errors"].append({"code": "invalid_provider_response"})
                articles = []
            for article in articles[:100]:
                if not isinstance(article, dict):
                    excluded += 1
                    continue
                content = article.get("content") if isinstance(article.get("content"), dict) else article
                title = content.get("title")
                url = content.get("canonicalUrl") or content.get("clickThroughUrl") or content.get("link") or ""
                if isinstance(url, dict):
                    url = url.get("url", "")
                try:
                    url, _, _ = safe_url(url)
                except (ValueError, TypeError):
                    excluded += 1
                    continue
                if not isinstance(title, str) or not title.strip() or url in seen:
                    excluded += 1
                    continue
                stamp = content.get("pubDate") or content.get("pub_date")
                if not stamp and content.get("providerPublishTime") is not None:
                    try:
                        stamp = datetime.fromtimestamp(float(content["providerPublishTime"]), timezone.utc).isoformat()
                    except (ValueError, TypeError, OverflowError, OSError):
                        stamp = None
                published = parse_stamp(stamp)
                if published and ((begin and published < begin) or published > end):
                    excluded += 1
                    continue
                seen.add(url)
                provider = content.get("provider") or {}
                publisher = provider.get("displayName") if isinstance(provider, dict) else None
                pubtime = published.isoformat() if published else None
                result["items"].append({
                    "candidate_id": "NC_" + hashlib.sha256(url.encode()).hexdigest()[:24],
                    "task_id": task.get("task_id"), "title": title.strip()[:600],
                    "url": url, "publisher": publisher or content.get("publisher") or None,
                    "pubtime": pubtime, "provider_published_at": pubtime,
                    "published_at": None, "actual_public_at": None,
                    "first_seen_at": collected, "status": "candidate_metadata",
                    "body_acquired": False, "source_verified": False, "eligible": False,
                    "historical_eligible": False,
                    "unknowns": ["original_body_not_acquired", "provider_metadata_unverified"]
                                + ([] if published else ["publication_time_unknown"])})
                if len(result["items"]) >= max_results:
                    break
            result["excluded_records"] = excluded
            if result["items"]:
                result["status"] = "candidates"
    result["seconds"] = round(time.perf_counter() - started, 6)
    if cache_dir is not None:
        try:
            target = Path(cache_dir).resolve()
            target.mkdir(parents=True, exist_ok=True)
            identity = hashlib.sha256((query + "|" + collected).encode()).hexdigest()[:24]
            path = target / ("yahoo_" + identity + ".json")
            result["cache_path"] = str(path)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            result.pop("cache_path", None)
            result["errors"].append({"code": "cache_write_unavailable"})
    return result


__all__ = ["search_yahoo", "fetch_article"]
