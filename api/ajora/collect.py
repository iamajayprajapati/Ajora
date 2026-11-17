import hashlib
import ipaddress
import json
import re
import socket
import ssl
import time
from datetime import date
from typing import NamedTuple
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import pymupdf
from bs4 import BeautifulSoup
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pydantic import ValidationError

from .event_dates import DEADLINE, dates_in
from .registration import (
    IMAGE_TYPES, MAX_POSTER_BYTES, MAX_POSTERS, POSTER, PRINTED_URL, REGISTER, ApplicationLink,
    bundle_html, distinct_links, image_links, make_link, pdf_links, poster_assets, unpack_html,
)
from .schema import Candidate, https_url

USER_AGENT = "AJORA/0.1"
EVENT_WORDS = re.compile(r"workshop|conference|hackathon|seminar|training|summer.school|lecture|symposium|webinar|\bfdp\b|event", re.I)
MAX_BYTES = 5_000_000
PARSER_VERSION = 8


def official(url, domains):
    if not https_url(url):
        return False
    host = urlsplit(url).hostname
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def normalized(value):
    return " ".join(str(value).split()).casefold()


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


class Collector:
    def __init__(self, domains, client=None, min_interval=2):
        self.domains = domains
        self.client = client or httpx.Client(timeout=18, trust_env=False, verify=ssl.create_default_context(),
                                             headers={"User-Agent": USER_AGENT})
        self.robots = {}
        self.last_request = {}
        self.min_interval = min_interval

    def close(self):
        self.client.close()

    def request(self, url, headers=None, limit=MAX_BYTES):
        if not official(url, self.domains):
            raise ValueError("URL is outside the approved institute domains")
        host = urlsplit(url).hostname
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(ip[4][0]).is_global for ip in addresses):
            raise ValueError("Source resolved to a non-public address")
        delay = self.min_interval - (time.monotonic() - self.last_request.get(host, 0))
        if delay > 0:
            time.sleep(delay)
        self.last_request[host] = time.monotonic()
        with self.client.stream("GET", url, headers=headers, follow_redirects=False) as response:
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("Source exceeds the collection size limit")
            return response.status_code, response.headers, bytes(data)

    def policy(self, url):
        host = urlsplit(url).netloc
        if host not in self.robots:
            robots_url = f"https://{host}/robots.txt"
            for _ in range(5):
                status, headers, body = self.request(robots_url, limit=500_000)
                if status not in (301, 302, 303, 307, 308):
                    break
                location = headers.get("location")
                target = urldefrag(urljoin(robots_url, location)).url if location else ""
                if not official(target, self.domains):
                    raise ValueError("Crawler policy redirect left the approved institute domains")
                robots_url = target
            parser = RobotFileParser()
            if status == 404:
                parser.allow_all = True
            elif status == 200:
                if re.search(rb"<(?:!doctype\s+html|html\b)", body[:2048], re.I):
                    raise ValueError("Crawler policy URL returned an HTML page")
                parser.parse(body.decode("utf-8", "replace").splitlines())
            else:
                raise ValueError(f"Unable to verify crawler policy (HTTP {status})")
            self.robots[host] = parser
        return self.robots[host]

    def posters(self, body, page_url):
        doc = BeautifulSoup(body, "html.parser")
        seen = set()
        assets = []
        for url, inline in poster_assets(doc, page_url):
            if inline is not None:
                continue  # Inline images are preserved in the HTML itself.
            if not official(url, self.domains) or urlsplit(url).hostname != urlsplit(page_url).hostname or url in seen:
                continue
            if len(seen) >= MAX_POSTERS:
                break
            seen.add(url)
            try:
                body, _, final_url, _ = self.fetch(url, media_only=True)
            except (ValueError, OSError, httpx.HTTPError):
                continue
            if body is not None:
                assets.append((final_url, body))
        return bool(seen), assets

    def fetch(self, url, etag=None, modified=None, *, media_only=False, collect_posters=True):
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if modified:
            headers["If-Modified-Since"] = modified
        for _ in range(4):
            policy = self.policy(url)
            if not policy.can_fetch(USER_AGENT, url):
                raise ValueError("Disallowed by robots.txt")
            delay = max(policy.crawl_delay(USER_AGENT) or 0, self.min_interval)
            rate = policy.request_rate(USER_AGENT)
            if rate and rate.requests:
                delay = max(delay, rate.seconds / rate.requests)
            if delay > 60:
                raise ValueError("Source crawl delay requires a separate monitoring schedule")
            host = urlsplit(url).hostname
            pause = delay - (time.monotonic() - self.last_request.get(host, 0))
            if pause > 0:
                time.sleep(pause)
            status, response_headers, body = self.request(url, headers, limit=MAX_POSTER_BYTES if media_only else MAX_BYTES)
            if status in (301, 302, 303, 307, 308):
                target = urldefrag(urljoin(url, response_headers.get("location", ""))).url
                if not official(target, self.domains):
                    raise ValueError("Redirect left the approved institute domains")
                if media_only and urlsplit(target).hostname != host:
                    raise ValueError("Poster redirect left the announcement host")
                url, headers = target, {}
                continue
            if status == 304:
                return None, response_headers, url, None
            if status != 200:
                raise ValueError(f"Source unavailable (HTTP {status})")
            content_type = response_headers.get("content-type", "").split(";", 1)[0].strip().lower()
            kind = ("image" if content_type in IMAGE_TYPES else "pdf" if body.startswith(b"%PDF") else "xml" if "xml" in content_type
                    else "html" if "html" in content_type else None)
            if not kind or media_only and kind != "image":
                raise ValueError("Source format requires manual collection")
            if kind == "html" and collect_posters:
                includes_posters, assets = self.posters(body, url)
                if includes_posters:
                    body, kind = bundle_html(body, assets), "html_bundle"
            return body, response_headers, url, kind
        raise ValueError("Too many redirects")


def classify(title):
    for pattern, category in (
        ("workshop", "Workshop"), ("conference|symposium", "Conference"),
        ("hackathon", "Hackathon"), ("seminar|lecture|webinar", "Seminar"),
        ("summer.school", "Summer school"), (r"\bfdp\b|faculty development", "FDP"),
        ("training|capacity building", "Training"),
    ):
        if re.search(pattern, title, re.I):
            return category
    return "Other"


def json_events(value):
    if isinstance(value, list):
        for item in value:
            yield from json_events(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        types = [types] if isinstance(types, str) else types
        if isinstance(types, list) and any(isinstance(t, str) and t.rsplit("/", 1)[-1].endswith("Event") for t in types):
            yield value
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            yield from json_events(value.get(key))


def iso_date(value):
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            pass
    return None


def extract_structured(raw, domains):
    title, start = raw.get("name"), iso_date(raw.get("startDate"))
    if not isinstance(title, str) or not start:
        return None
    category = classify(title)
    payload = {"title": title, "starts": start, "category": category}
    evidence = {"title": title, "starts": raw["startDate"]}
    if category != "Other":
        evidence["category"] = title
    if end := iso_date(raw.get("endDate")):
        payload["ends"], evidence["ends"] = end, raw["endDate"]
    status = raw.get("eventStatus")
    if status in ("https://schema.org/EventCancelled", "https://schema.org/EventPostponed"):
        payload["status"] = "cancelled" if status.endswith("Cancelled") else "postponed"
        evidence["status"] = status
    mode = raw.get("eventAttendanceMode")
    parsed_mode = {"https://schema.org/OnlineEventAttendanceMode": "online",
                   "https://schema.org/OfflineEventAttendanceMode": "offline",
                   "https://schema.org/MixedEventAttendanceMode": "hybrid"}.get(mode if isinstance(mode, str) else "")
    option = {"mode": parsed_mode}
    if parsed_mode:
        payload["attendance"] = [option]
        evidence["attendance.0.mode"] = mode
    offer = raw.get("offers")
    if isinstance(offer, dict) and offer.get("@type") in ("Offer", "https://schema.org/Offer"):
        price = offer.get("price")
        if parsed_mode in ("online", "offline") and offer.get("priceCurrency") == "INR" and re.fullmatch(r"[0-9]{1,8}(?:\.0+)?", str(price)):
            option["fee_inr"] = int(str(price).split(".")[0])
            evidence["attendance.0.fee_inr"] = '"price": ' + json.dumps(price, ensure_ascii=False)
        registration_url = offer.get("url")
        if isinstance(registration_url, str) and official(registration_url, domains):
            payload["registration_url"], evidence["registration_url"] = registration_url, registration_url
    place = raw.get("location")
    if isinstance(place, dict) and place.get("@type") in ("Place", "https://schema.org/Place") and isinstance(place.get("name"), str):
        payload["venue"], evidence["venue"] = place["name"], place["name"]
    organizer = raw.get("organizer")
    if isinstance(organizer, dict) and isinstance(organizer.get("name"), str):
        payload["entities"] = [{"name": organizer["name"], "role": "organizer"}]
        evidence["entities.0.name"] = organizer["name"]
        evidence["entities.0.role"] = '"organizer":'
    try:
        return Candidate.model_validate(payload), evidence
    except ValidationError:
        return None


class ParsedSource(NamedTuple):
    text: str
    discovered: list[tuple[str, str]]
    candidates: list[tuple[Candidate, dict[str, str]]]
    applications: list[ApplicationLink]


EVENT_DATE_LABEL = re.compile(
    r"^(?:(?:event|workshop|seminar|conference|programme|program|training|hackathon)\s+dates?|"
    r"dates?\s+of\s+(?:the\s+)?(?:event|workshop|conference))\s*[:–—-]", re.I,
)
BROCHURE_HEADING = re.compile(
    r"\b(?:workshop|conference|seminar|capacity building programme|training programme|hackathon)\b"
    r"[^\n]{0,35}(?:\bon\s*$|$)", re.I,
)
MODE_LABEL = re.compile(r"\((offline|online|in.person|hybrid)(?: event)?\)", re.I)


def labeled_candidate(title, category_quote, event_quotes, deadline_quotes, mode_quote=None):
    if not title or not event_quotes:
        return []
    ranges = {tuple(sorted(dates_in(quote))): quote for quote in event_quotes}
    if len(ranges) != 1:
        return []
    dates, quote = next(iter(ranges.items()))
    category = classify(category_quote)
    payload = {"title": title, "starts": dates[0], "category": category}
    proof = {"title": title, "starts": quote}
    if category != "Other":
        proof["category"] = category_quote
    if len(dates) == 2:
        payload["ends"], proof["ends"] = dates[1], quote
    deadlines = {next(iter(dates_in(line))): line for line in deadline_quotes}
    if len(deadlines) == 1:
        deadline, excerpt = next(iter(deadlines.items()))
        if deadline <= dates[-1]:
            payload["registration_deadline"], proof["registration_deadline"] = deadline, excerpt
    if mode_quote and (match := MODE_LABEL.search(mode_quote)):
        mode = match[1].lower()
        payload["attendance"] = [{"mode": "offline" if mode.startswith("in") else mode}]
        proof["attendance.0.mode"] = match[0]
    try:
        return [(Candidate.model_validate(payload), proof)]
    except ValidationError:
        return []


def pdf_candidates(cover, pages):
    lines = [line.strip() for line in cover.splitlines()]
    for index, heading in enumerate(lines[:50]):
        if not BROCHURE_HEADING.search(heading):
            continue
        cursor = index + 1
        while cursor < len(lines) and (not lines[cursor] or lines[cursor].lower() == "on"):
            cursor += 1
        title_parts = []
        for line in lines[cursor:cursor + 4]:
            if not line or MODE_LABEL.search(line) or dates_in(line) or not re.search(r"[a-zA-Z]", line):
                break
            title_parts.append(line)
        if not title_parts:
            continue
        title = " ".join(title_parts)
        page_lines = [[line.strip() for line in page.splitlines() if line.strip()] for page in pages]
        statements = [line for page in page_lines for line in page]
        pairs = [f"{a} {b}" for page in page_lines for a, b in zip(page, page[1:])]
        event_quotes = [line for line in statements if EVENT_DATE_LABEL.search(line) and 1 <= len(dates_in(line)) <= 2]
        # Unlabelled cover dates need a date-only range on one or two lines.
        if not event_quotes:
            cover_lines = [line for line in lines if line]
            options = cover_lines + [f"{a} {b}" for a, b in zip(cover_lines, cover_lines[1:])]
            event_quotes = [line for line in options if re.match(r"^(?:\d|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", line, re.I)
                            and re.search(r"\d{4}$", line) and len(line) <= 48 and len(dates_in(line)) == 2]
        deadlines = [line for line in statements + pairs if DEADLINE.match(line) and len(dates_in(line)) == 1]
        # Three split lines are common in flyer layouts.
        deadlines += [quote for page in page_lines for a, b, c in zip(page, page[1:], page[2:])
                      if DEADLINE.match(quote := f"{a} {b} {c}") and len(dates_in(quote)) == 1]
        # Prefer short, self-contained deadline statements for review.
        deadlines.sort(key=len, reverse=True)
        mode_quote = next((line for line in lines[:cursor + 8] if MODE_LABEL.search(line)), None)
        return labeled_candidate(title, heading, event_quotes, deadlines, mode_quote)
    return []


def calendar_candidates(doc, url):
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    path = urlsplit(url).path.rstrip("/").lower()
    results = []
    if host == "nitrkl.ac.in" and path == "/home/allevents":
        for card in doc.select(".continuing-education-list blockquote", limit=100):
            title = card.find("h5")
            icon = card.select_one("li .bi-calendar2-week")
            if title is None or icon is None:
                continue
            name = " ".join(text.strip() for text in title.find_all(string=True, recursive=False) if text.strip())
            dates = " ".join(text.strip() for text in icon.parent.find_all(string=True, recursive=False) if text.strip())
            if 1 <= len(dates_in(dates)) <= 2:
                results.extend(labeled_candidate(name, name, [dates], []))
    if host == "jnu.ac.in" and path.endswith("/jnuevents"):
        for row in doc.select("tr.marquee-row", limit=150):
            title = row.select_one(".views-field-title")
            columns = row.find_all("td", recursive=False)
            if title is None or len(columns) < 3:
                continue
            start, end = (cell.get_text(" ", strip=True) for cell in columns[:2])
            first, last = dates_in(start), dates_in(end)
            if len(first) != 1 or len(last) != 1:
                continue
            name = title.get_text(" ", strip=True)
            category = classify(name)
            try:
                candidate = Candidate(title=name, category=category, starts=next(iter(first)), ends=next(iter(last)))
            except ValidationError:
                continue
            evidence = {"title": name, "starts": start, "ends": end}
            if category != "Other":
                evidence["category"] = name
            results.append((candidate, evidence))
    return results


def parse(body, kind, url, domains):
    applications = []
    deadline_quotes = {}
    if kind == "image":
        return ParsedSource("", [], [], image_links(body, url))
    if kind == "pdf":
        pages = []
        with pymupdf.open(stream=body, filetype="pdf") as pdf:
            for page in pdf.pages(0, min(len(pdf), 35)):
                text, found = pdf_links(page)
                pages.append(text)
                applications.extend(found)
        content = "\n\n".join(f"[Page {index}]\n{text}" for index, text in enumerate(pages, 1))
        return ParsedSource(content, [], pdf_candidates(pages[0], pages) if pages else [], distinct_links(applications))
    if kind == "xml":
        try:
            root = ElementTree.fromstring(body)
        except (ElementTree.ParseError, DefusedXmlException) as error:
            raise ValueError("Invalid or unsupported XML feed") from error
        content = "\n".join(root.itertext())
        anchors = [(node.get("href") or node.text or "", " ".join(node.itertext()))
                   for node in root.iter() if node.tag.rsplit("}", 1)[-1] in ("link", "loc")]
        structured = []
        heading, event_quotes = "", []
        calendar = []
    else:
        if kind == "html_bundle":
            body, assets = unpack_html(body)
            for location, image in assets:
                applications.extend(image_links(image, location))
        doc = BeautifulSoup(body, "html.parser")
        calendar = calendar_candidates(doc, url)
        structured = []
        for script in doc.find_all("script", type="application/ld+json"):
            try:
                structured.extend(json_events(json.loads(script.get_text() or script.string or "")))
            except (ValueError, RecursionError):
                continue
        anchors = [(a["href"], " ".join(filter(None, (
            a.get_text(" ", strip=True), a.get("aria-label"), a.get("title"),
            *(img.get("alt") for img in a.find_all("img")),
        )))) for a in doc.find_all("a", href=True)]
        inline_count = 0
        for location, image in poster_assets(doc, url):
            if image is not None:
                applications.extend(image_links(image, location))
                inline_count += 1
                if inline_count >= MAX_POSTERS:
                    break
        headings = doc.find_all("h1")
        heading = headings[0].get_text(" ", strip=True) if len(headings) == 1 else ""
        event_quotes = []
        for tag in doc.find_all(["p", "li", "figcaption", "td", "tr"], limit=800):
            line = tag.get_text(" ", strip=True)
            if len(line) <= 150 and EVENT_DATE_LABEL.search(line) and 1 <= len(dates_in(line)) <= 2 and not DEADLINE.search(line):
                event_quotes.append(line)
            if len(line) <= 250 and DEADLINE.search(line) and len(dates := dates_in(line)) == 1:
                deadline_quotes.setdefault(next(iter(dates)), line)
            if REGISTER.search(line):
                for value in PRINTED_URL.findall(line):
                    if link := make_link(value.rstrip(".,);]"), "Announcement text", url, line):
                        applications.append(link)
        for tag in doc(["script", "style", "noscript"]):
            tag.decompose()
        content = doc.get_text(" ", strip=True)

    links = {}
    for href, label in anchors:
        try:
            resolved = urljoin(url, href.strip())
            target = urldefrag(resolved).url
        except ValueError:
            continue
        if REGISTER.search(label) and resolved != url:
            if link := make_link(resolved, "Announcement hyperlink", url, label):
                applications.append(link)
        if target == url or not official(target, domains):
            continue
        path = urlsplit(target).path.lower()
        is_poster = path.endswith((".png", ".jpeg", ".jpg", ".webp", ".gif")) and POSTER.search(f"{label} {path}")
        if is_poster or EVENT_WORDS.search(f"{target} {label}") or path.endswith(".pdf"):
            links[target] = label[:180]

    data = structured[:60]
    for raw in data:
        offer = raw.get("offers")
        if isinstance(offer, dict) and offer.get("@type") in ("Offer", "https://schema.org/Offer"):
            if link := make_link(offer.get("url"), "Structured event offer", url):
                applications.append(link)
    results = [result for raw in data if (result := extract_structured(raw, domains))]
    if len(results) == len(deadline_quotes) == 1 and len(data) == 1:
        candidate, evidence = results[0]
        deadline, quote = next(iter(deadline_quotes.items()))
        if deadline <= (candidate.ends or candidate.starts):
            results[0] = (candidate.model_copy(update={"registration_deadline": deadline}),
                          evidence | {"registration_deadline": quote})
    if not data and classify(heading) != "Other":
        results = labeled_candidate(heading, heading, event_quotes, deadline_quotes.values())
    if not results:
        results = calendar
    text = f"{content}\n" + "\n".join(json.dumps(raw, ensure_ascii=False) for raw in data)
    ranked = sorted(links.items(), key=lambda link: not bool(EVENT_WORDS.search(f"{link[0]} {link[1]}")))
    return ParsedSource(text, ranked[:18], results, distinct_links(applications))
