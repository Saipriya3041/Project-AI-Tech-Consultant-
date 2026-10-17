import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


DEFAULT_SITEMAP = "https://support.brilliantdirectories.com/support/sitemap.xml"


def parse_sitemap_xml(xml_content: bytes | str, source_url: str = DEFAULT_SITEMAP) -> tuple[list[dict[str, str | None]], list[str]]:
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


def fetch_sitemap(url: str = DEFAULT_SITEMAP, timeout: int = 15) -> list[dict[str, str | None]]:
    visited: set[str] = set()
    pages: dict[str, dict[str, str | None]] = {}
    pending = [url]
    while pending:
        sitemap_url = pending.pop()
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        request = Request(sitemap_url, headers={"User-Agent": "AITechConsultant/1.0 (polite knowledge indexing)"})
        with urlopen(request, timeout=timeout) as response:
            entries, child_sitemaps = parse_sitemap_xml(response.read(), sitemap_url)
        for entry in entries:
            pages[entry["url"]] = entry
        pending.extend(child_sitemaps)
    return list(pages.values())
