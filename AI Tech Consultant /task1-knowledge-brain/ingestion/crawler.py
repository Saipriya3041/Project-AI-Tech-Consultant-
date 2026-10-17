import os
import time
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from ingestion.sitemap import DEFAULT_SITEMAP, fetch_sitemap
from multimodal.vision import analyze_image
from retrieval.store import (
    article_needs_fetch,
    deactivate_removed_urls,
    get_article_links,
    get_pending_article_links,
    get_sitemap_cursor,
    record_failure,
    save_article,
    save_sitemap_cursor,
)


class ArticleParser(HTMLParser):
    def __init__(self, source_url: str):
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.title = ""
        self.in_title = False
        self.heading = ""
        self.sections: list[dict] = []
        self.parts: list[str] = []
        self.images: list[dict] = []
        self.links: list[str] = []
        self.last_context = ""
        self.suppressed_depth = 0
        self.suppressed_tags = {"script", "style", "nav", "footer", "header", "aside", "form", "noscript"}
        self.heading_tag: str | None = None

    def _flush(self) -> None:
        text = "\n".join(self.parts).strip()
        if text:
            self.sections.append({"heading": self.heading or self.title or self.source_url, "text": text})
        self.parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in self.suppressed_tags:
            self.suppressed_depth += 1
            return
        if self.suppressed_depth:
            return
        if tag == "title":
            self.in_title = True
        elif tag in {"h1", "h2", "h3", "h4"}:
            self._flush()
            self.heading_tag = tag
            self.heading = ""
        elif tag == "img":
            image_url = urljoin(self.source_url, attributes.get("src") or "")
            alt = (attributes.get("alt") or "").strip()
            if image_url:
                context = self.last_context or " ".join(self.parts[-3:])
                self.images.append({"url": image_url, "alt": alt, "context": context.strip()})
            if alt:
                self.parts.append(f"Image description (provided alt text, not vision-verified): {alt}")
        elif tag == "a":
            href = attributes.get("href")
            if href:
                link = urljoin(self.source_url, href.split("#", 1)[0])
                source = urlparse(self.source_url)
                target = urlparse(link)
                if target.scheme in {"http", "https"} and target.netloc == source.netloc and target.path.startswith("/support/solutions"):
                    self.links.append(link)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.suppressed_tags and self.suppressed_depth:
            self.suppressed_depth -= 1
            return
        if self.suppressed_depth:
            return
        if tag == "title":
            self.in_title = False
        elif self.heading_tag == tag:
            self.heading_tag = None
        elif tag in {"p", "li", "td", "th", "br"}:
            if tag != "br":
                text_parts = [part.strip() for part in self.parts if part.strip() and part != "\n"]
                if text_parts:
                    self.last_context = " ".join(text_parts[-3:])
            if self.parts and self.parts[-1] != "\n":
                self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self.in_title:
            self.title = f"{self.title} {text}".strip()
        elif self.heading_tag:
            self.heading = f"{self.heading} {text}".strip()
        elif not self.suppressed_depth:
            self.parts.append(text)


def parse_article(html: str, source_url: str) -> dict:
    parser = ArticleParser(source_url)
    parser.feed(html)
    parser._flush()
    return {
        "title": parser.title or source_url,
        "sections": parser.sections,
        "images": parser.images,
        "links": sorted(set(parser.links)),
    }


def _get_with_retries(url: str, attempts: int, timeout: int):
    last_error = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": "AITechConsultant/1.0 (polite knowledge indexing)"})
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as error:
            if error.code != 429 and error.code < 500:
                raise RuntimeError(f"Permanent HTTP error {error.code}: {url}") from error
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 8))
        except (URLError, TimeoutError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(str(last_error))


def run_ingestion(limit: int = 20, sitemap_url: str = DEFAULT_SITEMAP) -> dict:
    delay = float(os.getenv("CRAWL_DELAY_SECONDS", "0.5"))
    attempts = int(os.getenv("CRAWL_RETRIES", "3"))
    timeout = int(os.getenv("CRAWL_TIMEOUT_SECONDS", "15"))
    vision_enabled = os.getenv("VISION_ENABLED", "true").lower() == "true"
    max_images = max(0, int(os.getenv("VISION_MAX_IMAGES_PER_ARTICLE", "5")))
    vision_delay = max(0, float(os.getenv("VISION_REQUEST_DELAY_SECONDS", "1")))
    parsed_sitemap_url = urlparse(sitemap_url)
    robots_url = f"{parsed_sitemap_url.scheme}://{parsed_sitemap_url.netloc}/robots.txt"
    robots_request = Request(robots_url, headers={"User-Agent": "AITechConsultant/1.0"})
    with urlopen(robots_request, timeout=timeout) as response:
        robots = RobotFileParser(robots_url)
        robots.parse(response.read().decode("utf-8", errors="replace").splitlines())
    if not robots.can_fetch("AITechConsultant", sitemap_url):
        raise PermissionError("robots.txt disallows sitemap access")
    pages = fetch_sitemap(sitemap_url, timeout=timeout)
    unique_pages = {item["url"]: item for item in pages if item.get("url")}
    sitemap_entries = list(unique_pages.values())
    start_cursor = get_sitemap_cursor(sitemap_url, len(sitemap_entries))
    sitemap_pending = [
        (sitemap_entries[position]["url"], sitemap_entries[position].get("last_modified"), position)
        for position in [(start_cursor + offset) % len(sitemap_entries) for offset in range(len(sitemap_entries))]
    ] if sitemap_entries else []
    link_pending = get_pending_article_links()
    queued = {entry[0] for entry in sitemap_pending} | set(link_pending)
    queued_links = set(link_pending)
    visited: set[str] = set()
    next_cursor = start_cursor
    sitemap_pages_seen = 0
    result = {
        "discovered": len(unique_pages),
        "discovered_articles": len(queued),
        "processed": 0,
        "added_or_updated": 0,
        "unchanged": 0,
        "failed": 0,
        "disallowed": 0,
        "removed": 0,
        "complete": False,
    }
    while (sitemap_pending or link_pending) and len(visited) < limit:
        if sitemap_pages_seen == 0 and sitemap_pending or not link_pending:
            url, last_modified, sitemap_position = sitemap_pending.pop(0)
            is_sitemap_page = True
        else:
            url = link_pending.pop(0)
            last_modified = None
            sitemap_position = None
            is_sitemap_page = False
        if url in visited:
            if is_sitemap_page:
                next_cursor = (sitemap_position + 1) % len(sitemap_entries)
                sitemap_pages_seen += 1
                save_sitemap_cursor(sitemap_url, next_cursor)
            continue
        visited.add(url)
        if is_sitemap_page:
            next_cursor = (sitemap_position + 1) % len(sitemap_entries)
            sitemap_pages_seen += 1
            save_sitemap_cursor(sitemap_url, next_cursor)
        if not robots.can_fetch("AITechConsultant", url):
            result["disallowed"] += 1
            continue
        if not article_needs_fetch(url, last_modified):
            result["unchanged"] += 1
            links = get_article_links(url)
        else:
            try:
                html = _get_with_retries(url, attempts, timeout)
                parsed = parse_article(html, url)
                if vision_enabled:
                    for image in parsed["images"][:max_images]:
                        image.update(analyze_image(image["url"], image.get("context", "")))
                        if image.get("status") == "analyzed":
                            time.sleep(vision_delay)
                changed = save_article(url, parsed["title"], parsed["sections"], last_modified, parsed["images"], parsed["links"])
                result["processed"] += 1
                result["added_or_updated" if changed else "unchanged"] += 1
                links = parsed["links"]
            except Exception as error:
                record_failure(url, str(error))
                result["failed"] += 1
                links = []
            time.sleep(max(0, delay))
        for link in reversed(links):
            if link not in queued_links and link not in visited:
                link_pending.insert(0, link)
                queued_links.add(link)
                queued.add(link)

    result["discovered_articles"] = len(queued)
    pending_links = get_pending_article_links()
    result["complete"] = (
        bool(sitemap_entries)
        and sitemap_pages_seen > 0
        and next_cursor == 0
        and not pending_links
        and not link_pending
        and result["failed"] == 0
        and result["disallowed"] == 0
    )
    if result["complete"]:
        result["removed"] = deactivate_removed_urls(queued)
    return result