import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from urllib.request import Request
from ingestion.safe_http import open_public_url


class SitemapPages(list[dict[str, str | None]]):
    def __init__(self, pages: list[dict[str, str | None]], duplicate_urls_skipped: int):
        super().__init__(pages)
        self.duplicate_urls_skipped = duplicate_urls_skipped


def _looks_like_xml(content: bytes) -> bool:
    """Return True if the bytes look like an XML document (starts with XML declaration or a tag)."""
    head = content[:200].lstrip()
    return head.startswith(b"<?xml") or head.startswith(b"<sitemapindex") or head.startswith(b"<urlset")


def resolve_sitemap_url(url: str, timeout: int = 15) -> str:
    """Given any HTTP/HTTPS URL, return a sitemap URL to crawl.

    If the URL already points to an XML sitemap (ends with .xml / .xml.gz or
    contains 'sitemap' in the path) it is returned as-is.  Otherwise the
    function tries the common discovery locations in order:
      1. <scheme>://<host>/sitemap.xml
      2. <scheme>://<host>/sitemap_index.xml
      3. The Sitemap: directive(s) in <scheme>://<host>/robots.txt
    The first location that returns valid XML is used; if none is found the
    original URL is returned unchanged (so the caller can handle the error).
    """
    parsed = urlparse(url)
    path = parsed.path.lower()
    # Already looks like a sitemap — return directly
    if path.endswith(".xml") or path.endswith(".xml.gz") or "sitemap" in path:
        return url

    base = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
    ]

    # Try to read robots.txt for Sitemap: directives
    try:
        robots_request = Request(
            f"{base}/robots.txt",
            headers={"User-Agent": "AITechConsultant/1.0 (polite knowledge indexing)"},
        )
        with open_public_url(robots_request, timeout=timeout) as response:
            robots_text = response.read().decode("utf-8", errors="replace")
        for line in robots_text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("sitemap:"):
                sitemap_directive = stripped.split(":", 1)[1].strip()
                if sitemap_directive.startswith(("http://", "https://")):
                    candidates.insert(0, sitemap_directive)
    except Exception:
        pass

    for candidate in candidates:
        try:
            req = Request(
                candidate,
                headers={"User-Agent": "AITechConsultant/1.0 (polite knowledge indexing)"},
            )
            with open_public_url(req, timeout=timeout) as response:
                content = response.read()
            if _looks_like_xml(content):
                return candidate
        except Exception:
            continue

    # Fall back to the original URL; fetch_sitemap will fail with a clear error
    return url


class SitemapPages(list[dict[str, str | None]]):
    def __init__(self, pages: list[dict[str, str | None]], duplicate_urls_skipped: int):
        super().__init__(pages)
        self.duplicate_urls_skipped = duplicate_urls_skipped


def parse_sitemap_xml(xml_content: bytes | str, source_url: str = "") -> tuple[list[dict[str, str | None]], list[str]]:
    root = ET.fromstring(xml_content)
    entries: list[dict[str, str | None]] = []
    child_sitemaps: list[str] = []
    root_kind = root.tag.rsplit("}", 1)[-1]
    item_kind = "sitemap" if root_kind == "sitemapindex" else "url"
    for element in root:
        if element.tag.rsplit("}", 1)[-1] != item_kind:
            continue
        fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in element}
        location = fields.get("loc")
        if not location:
            continue
        absolute_url = urljoin(source_url, location)
        if urlparse(absolute_url).scheme not in {"http", "https"}:
            continue
        if root_kind == "sitemapindex":
            child_sitemaps.append(absolute_url)
        elif root_kind == "urlset":
            entries.append({"url": absolute_url, "last_modified": fields.get("lastmod")})
    return entries, child_sitemaps


def fetch_sitemap_details(url: str = "", timeout: int = 15) -> dict:
    # Auto-discover the sitemap URL if a plain website URL was given
    resolved_url = resolve_sitemap_url(url, timeout=timeout) if url else url
    visited: set[str] = set()
    pages: dict[str, dict[str, str | None]] = {}
    url_occurrence_counts: dict[str, int] = {}
    url_sitemap_references: dict[str, list[str]] = {}
    pending = [(resolved_url, None)]
    sitemaps = []
    while pending:
        sitemap_url, parent_url = pending.pop(0)
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        request = Request(sitemap_url, headers={"User-Agent": "AITechConsultant/1.0 (polite knowledge indexing)"})
        with open_public_url(request, timeout=timeout) as response:
            entries, child_sitemaps = parse_sitemap_xml(response.read(), sitemap_url)
        sitemap_urls = set()
        for entry in entries:
            page_url = entry["url"]
            sitemap_urls.add(page_url)
            url_occurrence_counts[page_url] = url_occurrence_counts.get(page_url, 0) + 1
            references = url_sitemap_references.setdefault(page_url, [])
            if sitemap_url not in references:
                references.append(sitemap_url)
            pages.setdefault(page_url, entry)
        sitemaps.append({
            "url": sitemap_url,
            "parent_url": parent_url,
            "urls_found": len(entries),
            "duplicate_urls_skipped": len(entries) - len(sitemap_urls),
            "first_url": entries[0]["url"] if entries else None,
            "last_url": entries[-1]["url"] if entries else None,
            "nested": parent_url is not None,
        })
        pending.extend((child_url, sitemap_url) for child_url in child_sitemaps)
    unique_pages = list(pages.values())
    duplicate_urls = [
        {
            "url": page_url,
            "occurrences": occurrence_count,
            "sitemap_references": url_sitemap_references[page_url],
        }
        for page_url, occurrence_count in url_occurrence_counts.items()
        if occurrence_count > 1
    ]
    return {
        "pages": unique_pages,
        "sitemaps": sitemaps,
        "url_occurrence_counts": url_occurrence_counts,
        "url_sitemap_references": url_sitemap_references,
        "duplicate_urls": duplicate_urls,
        "total_sitemaps_found": len(sitemaps),
        "total_urls_found": len(unique_pages),
        "duplicate_urls_skipped": sum(url_occurrence_counts.values()) - len(unique_pages),
        "first_url": unique_pages[0]["url"] if unique_pages else None,
        "last_url": unique_pages[-1]["url"] if unique_pages else None,
    }


def fetch_sitemap(url: str = "", timeout: int = 15) -> list[dict[str, str | None]]:
    details = fetch_sitemap_details(url, timeout)
    return SitemapPages(details["pages"], details["duplicate_urls_skipped"])
