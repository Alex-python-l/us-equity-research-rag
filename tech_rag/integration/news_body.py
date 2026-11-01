"""Public HTML acquisition and immutable news paragraph provenance.

No login, hidden articleBody extraction, PDF parser, live historical fallback,
search-snippet promotion, credentials, model calls, or automatic HTTP retries.
"""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import queue
import re
import socket
import sys
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

MAX_RESPONSE_BYTES = 2_000_000
MAX_FETCH_SECONDS = 20
MAX_REDIRECTS = 3


def parse_stamp(value):
    """An ambiguous date or timezone never becomes a precise publication time."""
    if not isinstance(value, str) or "T" not in value and " " not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _is_global(address):
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast and not ip.is_unspecified


def safe_url(value):
    """Syntax/literal protection, also used for untrusted search metadata."""
    if not isinstance(value, str) or len(value) > 4096 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError("unsafe_url")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").rstrip(".").lower().encode("idna").decode("ascii")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if (parts.scheme not in ("http", "https") or not host or parts.username or parts.password
                or port not in (80, 443) or "%" in host
                or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home"))):
            raise ValueError("unsafe_url")
        try:
            if not _is_global(host):
                raise ValueError("unsafe_url")
        except ValueError as exc:
            # A domain is allowed to proceed to DNS; malformed numeric hosts are not.
            if str(exc) == "unsafe_url" or ":" in host or re.fullmatch(r"[0-9.]+", host):
                raise ValueError("unsafe_url") from None
        host_field = "[" + host + "]" if ":" in host else host
        if port != (443 if parts.scheme == "https" else 80):
            host_field += ":" + str(port)
        return urlunsplit((parts.scheme, host_field, parts.path or "/", parts.query, "")), host, port
    except (UnicodeError, TypeError, AttributeError):
        raise ValueError("unsafe_url") from None


def _resolve_public(host, port, remaining):
    # OS DNS may block independently of requests' connect timeout.
    output = queue.Queue(maxsize=1)

    def resolve():
        try:
            output.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError:
            output.put(None)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        entries = output.get(timeout=min(3, max(0.001, remaining)))
    except queue.Empty:
        raise TimeoutError("dns_timeout") from None
    if not entries:
        raise OSError("dns_unavailable")
    addresses = list(dict.fromkeys(entry[4][0] for entry in entries))
    if not addresses or any(not _is_global(address) for address in addresses):
        raise ValueError("unsafe_url")
    return addresses[0]


class PinnedHTTPAdapter(HTTPAdapter):
    """Connect to the validated IP, retaining the original TLS name and Host.

    Pinning closes the DNS-rebinding gap between validation and requests' own
    resolution. Requests' TLS verification stays enabled.
    """
    def __init__(self, hostname, address):
        self.hostname = hostname
        self.address = address
        super().__init__(max_retries=0)

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        _, host, _ = safe_url(request.url)
        if host != self.hostname or proxies or verify is not True:
            raise ValueError("unsafe_connection")
        host_params, kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        host_params["host"] = self.address
        if host_params["scheme"] == "https":
            kwargs.update(assert_hostname=self.hostname, server_hostname=self.hostname)
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=kwargs)


def _soup(raw):
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor/html_scope"))
        from bs4 import BeautifulSoup
    return BeautifulSoup(raw, "html.parser")


def _norm(text):
    return re.sub(r"\s+", " ", text).strip()


def _article_metadata(soup):
    published, modified = [], []
    facts = {"title": None, "publisher": None, "restricted": False,
             "article_schema": False, "time_metadata": []}

    def remember(kind, value, origin):
        if isinstance(value, str):
            facts["time_metadata"].append({"kind": kind, "value": value[:200], "origin": origin})
            stamp = parse_stamp(value)
            if stamp:
                (published if kind == "published" else modified).append(stamp)

    def walk(obj, depth=0):
        if depth > 12:
            return
        if isinstance(obj, list):
            for value in obj[:100]:
                walk(value, depth + 1)
        elif isinstance(obj, dict):
            types = obj.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if isinstance(types, list) and any(t in ("NewsArticle", "Article", "Report", "BlogPosting", "TechArticle") for t in types):
                facts["article_schema"] = True
                remember("published", obj.get("datePublished"), "jsonld:datePublished")
                remember("modified", obj.get("dateModified"), "jsonld:dateModified")
                if obj.get("isAccessibleForFree") in (False, "false", "False"):
                    facts["restricted"] = True
                if isinstance(obj.get("headline"), str):
                    facts["title"] = facts["title"] or obj["headline"]
                publisher = obj.get("publisher")
                if isinstance(publisher, dict) and isinstance(publisher.get("name"), str):
                    facts["publisher"] = facts["publisher"] or publisher["name"]
            # Only structured graph containers, not arbitrary unrelated articles.
            for key in ("@graph", "mainEntity"):
                if key in obj:
                    walk(obj[key], depth + 1)

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}, limit=20):
        text = script.string or script.get_text()
        if len(text) > 250_000:
            continue
        try:
            walk(json.loads(text))
        except (ValueError, TypeError, RecursionError):
            continue
    publish_keys = {"article:published_time", "datepublished", "pubdate", "publishdate", "dc.date.issued", "dcterms.issued"}
    modify_keys = {"article:modified_time", "datemodified", "lastmod", "dc.date.modified", "dcterms.modified"}
    for meta in soup.find_all("meta"):
        key = str(meta.get("property") or meta.get("name") or meta.get("itemprop") or "").lower()
        value = meta.get("content")
        if key in publish_keys:
            remember("published", value, "meta:" + key)
        elif key in modify_keys:
            remember("modified", value, "meta:" + key)
        elif key == "og:site_name" and isinstance(value, str):
            facts["publisher"] = facts["publisher"] or value
        elif key == "og:title" and isinstance(value, str):
            facts["title"] = facts["title"] or value
        elif key == "og:type" and value == "article":
            facts["article_schema"] = True
        elif key in ("robots", "googlebot") and isinstance(value, str) and "noarchive" in value.lower():
            facts["noarchive"] = True
    for node in soup.find_all("time"):
        prop = str(node.get("itemprop") or "").lower()
        if prop in ("datepublished", "datemodified"):
            remember("published" if prop == "datepublished" else "modified", node.get("datetime"), "time:" + prop)
    facts["published_times"] = sorted(set(published))
    facts["modified_times"] = sorted(set(modified))
    return facts


def _body_blocks(soup, facts, version, max_chars):
    root = soup.find("article") or soup.find(attrs={"itemprop": "articleBody"}) or soup.find("main")
    if root is None:
        root = soup.select_one(".article-body, .article-content, .story-body, .entry-content")
    if root is None and facts["article_schema"]:
        root = soup.body
    if root is None:
        return [], "", False, False
    for tag in root.find_all(["script", "style", "noscript", "nav", "footer", "form", "aside", "button"]):
        tag.decompose()
    for tag in root.find_all(True):
        if tag.attrs is None:  # A hidden ancestor was already removed.
            continue
        style = str(tag.get("style") or "")
        if (tag.has_attr("hidden") or tag.get("aria-hidden") == "true"
                or re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", style, re.I)):
            tag.decompose()
    blocks, texts, headings = [], [], {}
    used, prose_chars = 0, 0
    complete = True
    tags = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "table"]
    for node in root.find_all(tags):
        if node.name != "table" and node.find_parent("table"):
            continue
        if node.find_parent(["p", "li", "blockquote"]) is not None:
            continue
        if node.has_attr("hidden") or node.get("aria-hidden") == "true":
            continue
        if node.name == "table":
            rows, grid = [], []
            caption = node.find("caption")
            table = {"caption": _norm(caption.get_text(" ", strip=True)) if caption else None, "rows": grid}
            if caption:
                rows.append(_norm(caption.get_text(" ", strip=True)))
            for row in node.find_all("tr"):
                cells = row.find_all(["th", "td"], recursive=False)
                if cells:
                    rows.append(" | ".join(_norm(cell.get_text(" ", strip=True)) for cell in cells))
                    grid.append([{"text": _norm(cell.get_text(" ", strip=True)),
                                  "header": cell.name == "th",
                                  "scope": cell.get("scope"),
                                  "rowspan": int(cell.get("rowspan", "1")) if str(cell.get("rowspan", "1")).isdigit() else 1,
                                  "colspan": int(cell.get("colspan", "1")) if str(cell.get("colspan", "1")).isdigit() else 1}
                                 for cell in cells])
            text = "\n".join(rows)
        else:
            text = _norm(node.get_text(" ", strip=True))
        if not text:
            continue
        if used + (1 if blocks else 0) + len(text) > max_chars:
            complete = False
            break  # Keep complete blocks; never silently clip a table/context.
        if node.name.startswith("h"):
            level = int(node.name[1])
            headings = {key: val for key, val in headings.items() if key < level}
            headings[level] = text
        start = used + (1 if blocks else 0)
        index = len(blocks) + 1
        blocks.append({"citation_id": "N_" + version[5:] + "_" + str(index).zfill(4),
                       "paragraph": index, "text": text,
                       "kind": "table" if node.name == "table" else ("heading" if node.name.startswith("h") else "paragraph"),
                       "publisher_markup": node.name,
                       "heading_path": [headings[k] for k in sorted(headings)],
                       "char_start": start, "char_end": start + len(text)})
        if node.name == "table":
            blocks[-1]["table"] = table
        used = start + len(text)
        texts.append(text)
        if node.name in ("p", "li", "blockquote"):
            prose_chars += len(text)
    return blocks, "\n".join(texts), complete, prose_chars >= 80


def fetch_article(item: dict, as_of: str, *, historical=False, archive_dir: Path,
                  max_chars=18000) -> dict:
    """Fetch at most one public HTML page plus three explicitly checked redirects.

    ``eligible`` is for current collected evidence; ``as_of_eligible`` remains
    false because a mutable page is not an independently verified past version.
    Missing/ambiguous original publication metadata leaves a candidate only.
    """
    started = time.perf_counter()
    cutoff = parse_stamp(as_of)
    if cutoff is None:
        raise ValueError("as_of requires an ISO timestamp with timezone")
    if type(max_chars) is not int or not 1 <= max_chars <= 18000:
        raise ValueError("max_chars must be 1..18000")
    if not isinstance(item, dict):
        raise ValueError("item must be a dictionary")
    result = {"provider": "public_original_html", "candidate_id": item.get("candidate_id"),
              "url": item.get("url"), "final_url": None,
              "title": item.get("title"), "publisher": None,
              "search_title": item.get("title"), "search_publisher": item.get("publisher"),
              "as_of": cutoff.isoformat(), "status": "candidate_metadata",
              "published_at": None, "modified_at": None, "event_time": None,
              "first_retrieved_at": None, "retrieved_at": None,
              "body_sha256": None, "raw_sha256": None, "paragraphs": [],
              "body_text": "", "raw_path": None, "json_path": None, "version_id": None,
              "body_acquired": False, "source_verified": False, "eligible": False,
              "body_complete": False, "historical_eligible": False,
              "as_of_eligible": False, "version_as_of_verified": False,
              "version_scope": "current_as_collected", "retrieved_after_cutoff": False,
              "calls": 0, "seconds": 0.0, "real_network_executed": False,
              "unknowns": ["event_time_not_explicitly_verified", "historical_version_not_verified"], "errors": []}

    def finish(status, error=None):
        result["status"] = status
        result["seconds"] = round(time.perf_counter() - started, 6)
        if error:
            result["errors"].append({"code": error})
        return result

    if historical:
        return finish("unsupported_historical_body", "live_html_forbidden_for_historical")
    url = item.get("url")
    raw = None
    for redirect in range(MAX_REDIRECTS + 1):
        response = None
        try:
            url, host, port = safe_url(url)
            remaining = MAX_FETCH_SECONDS - (time.perf_counter() - started)
            if remaining <= 0:
                return finish("fetch_unavailable", "total_timeout")
            address = _resolve_public(host, port, remaining)
            remaining = MAX_FETCH_SECONDS - (time.perf_counter() - started)
            if remaining <= 0:
                return finish("fetch_unavailable", "total_timeout")
            with requests.Session() as session:
                session.trust_env = False
                adapter = PinnedHTTPAdapter(host, address)
                session.mount(urlsplit(url).scheme + "://", adapter)
                result["calls"] += 1
                result["real_network_executed"] = True
                response = session.get(url, headers={"User-Agent": "TechRAGNewsResearch/5.0", "Accept": "text/html, application/xhtml+xml", "Host": urlsplit(url).netloc},
                                       timeout=(min(4, remaining), min(5, remaining)),
                                       stream=True, allow_redirects=False)
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location or redirect >= MAX_REDIRECTS:
                        return finish("redirect_limit", "redirect_limit")
                    url = urljoin(url, location)
                    continue
                result["http_status"] = response.status_code
                if response.status_code in (401, 403, 407, 429):
                    return finish("access_restricted", "http_access_restricted")
                if response.status_code != 200:
                    return finish("fetch_unavailable", "http_error")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in ("text/html", "application/xhtml+xml"):
                    return finish("unsupported_content_type", "only_public_html_supported")
                if "noarchive" in response.headers.get("X-Robots-Tag", "").lower():
                    return finish("archiving_disallowed", "publisher_noarchive")
                length = response.headers.get("Content-Length", "")
                if length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
                    return finish("response_too_large", "response_byte_limit")
                chunks, count = [], 0
                for chunk in response.iter_content(chunk_size=8192):
                    if time.perf_counter() - started > MAX_FETCH_SECONDS:
                        return finish("fetch_unavailable", "total_timeout")
                    count += len(chunk)
                    if count > MAX_RESPONSE_BYTES:
                        return finish("response_too_large", "response_byte_limit")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                result["final_url"] = url
                break
        except (ValueError, UnicodeError):
            return finish("unsafe_url", "public_http_url_required")
        except (requests.RequestException, OSError, TimeoutError):
            return finish("fetch_unavailable", "network_or_dns_unavailable")
        finally:
            if response is not None:
                response.close()
    if not raw or raw.lstrip().startswith(b"%PDF-"):
        return finish("unsupported_content_type", "no_html_body")
    retrieved = datetime.now(timezone.utc)
    result.update(retrieved_at=retrieved.isoformat(), first_retrieved_at=retrieved.isoformat(),
                  retrieved_after_cutoff=retrieved > cutoff)
    soup = _soup(raw)
    facts = _article_metadata(soup)
    if facts["restricted"] or soup.select_one(".paywall, #paywall, .subscription-wall, #subscription-wall, [data-paywall='true'], #regwall"):
        return finish("access_restricted", "publisher_access_restricted")
    if facts.get("noarchive"):
        return finish("archiving_disallowed", "publisher_noarchive")
    published = facts["published_times"][0] if len(facts["published_times"]) == 1 else None
    modified = facts["modified_times"][-1] if facts["modified_times"] else None
    if len(facts["published_times"]) > 1:
        result["unknowns"].append("conflicting_publication_times")
    if len(facts["modified_times"]) > 1:
        result["unknowns"].append("conflicting_modification_times")
    if published is None:
        result["unknowns"].append("publication_time_unknown")
    if modified is None:
        result["unknowns"].append("modification_time_unknown")
    if published and published > cutoff:
        result["unknowns"].append("publication_after_cutoff")
    if modified and published and modified < published:
        result["unknowns"].append("modification_before_publication")
    if modified and modified > cutoff:
        result["unknowns"].append("modified_after_cutoff")
    result.update(published_at=published.isoformat() if published else None,
                  modified_at=modified.isoformat() if modified else None,
                  time_metadata=facts["time_metadata"], publisher=facts["publisher"])
    title = facts["title"] or (soup.h1.get_text(" ", strip=True) if soup.h1 else None) or (soup.title.get_text(" ", strip=True) if soup.title else None)
    result["title"] = _norm(title)[:600] if title else None
    raw_sha = hashlib.sha256(raw).hexdigest()
    version = "NEWS_" + hashlib.sha256((url + "|" + raw_sha).encode()).hexdigest()[:24]
    paragraphs, body_text, complete, acquired = _body_blocks(soup, facts, version, max_chars)
    if not complete:
        result["unknowns"].append("body_char_limit")
    if not acquired:
        paragraphs, body_text = [], ""
        result["unknowns"].append("substantive_article_body_unavailable")
    result.update(version_id=version, raw_sha256=raw_sha, paragraphs=paragraphs,
                  body_text=body_text, body_complete=complete and acquired,
                  body_sha256=hashlib.sha256(body_text.encode()).hexdigest() if acquired else None,
                  body_acquired=acquired,
                  source_verified=bool(acquired and published and len(facts["published_times"]) == 1))
    result["eligible"] = bool(result["source_verified"] and complete and published <= cutoff
                              and (not modified or published <= modified <= cutoff)
                              and len(facts["modified_times"]) <= 1 and result["title"])
    try:
        directory = Path(archive_dir).resolve() / version
        directory.mkdir(parents=True, exist_ok=True)
        raw_path = directory / "source.html"
        json_path = directory / ((result["body_sha256"] or "candidate") + ".json")
        if raw_path.exists():
            if hashlib.sha256(raw_path.read_bytes()).hexdigest() != raw_sha:
                result["eligible"] = False
                return finish("archive_conflict", "raw_archive_hash_mismatch")
        else:
            with raw_path.open("xb") as output:
                output.write(raw)
        if json_path.exists():
            previous = json.loads(json_path.read_text(encoding="utf-8"))
            if previous.get("raw_sha256") != raw_sha or previous.get("paragraphs") != paragraphs:
                result["eligible"] = False
                return finish("archive_conflict", "parsed_archive_mismatch")
            result["first_retrieved_at"] = previous["first_retrieved_at"]
        result.update(raw_path=str(raw_path), json_path=str(json_path))
        finish("body_verified" if result["eligible"] else "candidate_body")
        if not json_path.exists():
            with json_path.open("x", encoding="utf-8") as output:
                json.dump(result, output, ensure_ascii=False, indent=2)
    except (OSError, ValueError, KeyError):
        result["eligible"] = False
        return finish("archive_unavailable", "archive_write_or_integrity_error")
    return result
