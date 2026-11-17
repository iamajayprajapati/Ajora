"""Find official News, Announcements, and Events sections for AJORA's directory."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import ssl
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup
import httpx
from sqlalchemy import select

from .collect import Collector, official, parse
from .db import Base, SessionLocal, Source, engine, now
from .registration import unpack_html
from .registry import DIRECTORY, current_website
from .seed import seed
from .workflow import today


SECTION = re.compile(r"\b(?:latest|recent|upcoming|all)\s+(?:news|events?|notices?|announcements?)\b|"
                     r"\b(?:news|events?|notices?|announcements?|circulars?|event calendar)\b", re.I)
SECTION_LABEL = re.compile(r"[\s&/|]+")
MEDIA = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".zip")
SECTION_ROUTE = re.compile(r"(?:all|recent|latest|upcoming|campus)?(?:events|news|announcements|notices)"
                           r"(?:\.(?:html?|php|aspx))?$")
KNOWN_SECTIONS = {
    "iit-bombay": "https://www.iitb.ac.in/events",
    "iit-madras": "https://www.iitm.ac.in/happenings/events",
    "nit-rourkela": "https://www.nitrkl.ac.in/Home/AllEvents/",
    "nit-surathkal": "https://www.nitk.ac.in/upcoming_events",
    "iiit-allahabad": "https://iris.iiita.ac.in/events.php",
    "jnu": "https://www.jnu.ac.in/jnuevents/",
    "delhi-university": "https://www.du.ac.in/?page=events",
}


def section_links(body, url, domains):
    page = BeautifulSoup(body, "html.parser")
    found = {}
    for anchor in page.find_all("a", href=True, limit=5000):
        label = " ".join(anchor.stripped_strings) or anchor.get("aria-label", "")
        try:
            target = urldefrag(urljoin(url, anchor["href"])).url
            path = unquote(urlsplit(target).path).casefold()
        except ValueError:
            continue
        if target == url or not official(target, domains) or path.endswith(MEDIA):
            continue
        # Exclude article titles that merely mention an event.
        generic_label = len(label) <= 65 and all(SECTION.search(piece) or piece.lower() in {
            "and", "all", "latest", "recent", "upcoming", "academic", "view", "read", "more",
            "press", "releases", "board", "archive", "calendar", "campus", "institute", "university",
        } for piece in SECTION_LABEL.split(label.strip()) if piece)
        route = path.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ")
        if (generic_label and SECTION.search(label)) or SECTION_ROUTE.fullmatch(route.replace(" ", "")):
            found.setdefault(target, label or route)
    return [{"url": target, "label": label} for target, label in list(found.items())[:8]]


def inspect_section(collector, section, domains):
    try:
        body, _, url, kind = collector.fetch(section["url"], collect_posters=False)
        parsed = parse(body, kind, url, domains)
        events = [{"title": event.title, "starts": event.starts.isoformat(),
                   "ends": event.ends.isoformat() if event.ends else None,
                   "registration_deadline": event.registration_deadline.isoformat() if event.registration_deadline else None,
                   "evidence": evidence}
                  for event, evidence in parsed.candidates if event.status == "scheduled" and (event.ends or event.starts) >= today()]
        section.update(status="checked", resolved_url=url, upcoming_candidates=events,
                       linked_announcements=[{"url": target, "label": label} for target, label in parsed.discovered])
    except (OSError, ValueError, httpx.HTTPError) as error:
        section.update(status="unavailable", error=str(error)[:200])


def scan(entry, inspect=False):
    home = current_website(entry)
    domains = [entry.domain, urlsplit(home).hostname.removeprefix("www.")]
    collector = Collector(domains, client=httpx.Client(timeout=httpx.Timeout(8), trust_env=False,
                                                       verify=ssl.create_default_context(),
                                                       headers={"User-Agent": "AJORA/0.1"}))
    row = {"institute_id": entry.id, "homepage": home, "checked_on": now(), "sections": []}
    try:
        body, _, resolved, kind = collector.fetch(home, collect_posters=False)
        if kind not in ("html", "html_bundle"):
            raise ValueError("Homepage did not return HTML")
        if kind == "html_bundle":
            body, _ = unpack_html(body)
        row.update(status="checked", homepage=resolved, sections=section_links(body, resolved, domains))
        known = KNOWN_SECTIONS.get(entry.id)
        if known and not any(section["url"] == known for section in row["sections"]):
            row["sections"].insert(0, {"url": known, "label": "Official events calendar"})
        if inspect:
            for section in row["sections"]:
                inspect_section(collector, section, domains)
    except (OSError, ValueError, httpx.HTTPError) as error:
        row.update(status="unavailable", error=str(error)[:200])
    finally:
        collector.close()
    return row


def queue_sections(db, rows):
    existing = set(db.scalars(select(Source.url)))
    added = 0
    for row in rows:
        entry = DIRECTORY.get(row["institute_id"])
        if not entry:
            continue
        home = current_website(entry)
        domains = [entry.domain, urlsplit(home).hostname.removeprefix("www.")]
        for section in row["sections"]:
            url = section["url"]
            if url not in existing and official(url, domains) and len(url) <= 1000:
                db.add(Source(institute_id=entry.id, url=url))
                existing.add(url)
                added += 1
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queue", action="store_true", help="Add discovered sections as pending sources")
    parser.add_argument("--inspect-sections", action="store_true", help="Fetch linked sections and report detected events")
    parser.add_argument("--institute", action="append", choices=DIRECTORY)
    args = parser.parse_args()
    entries = [DIRECTORY[key] for key in args.institute] if args.institute else list(DIRECTORY.values())
    with ThreadPoolExecutor(max_workers=12) as workers:
        rows = list(workers.map(lambda entry: scan(entry, args.inspect_sections), entries))
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.queue:
        Base.metadata.create_all(engine)
        with SessionLocal.begin() as db:
            seed(db)
            print(f"Pending sources added: {queue_sections(db, rows)}")
    print(f"Homepages checked: {sum(row['status'] == 'checked' for row in rows)}/{len(rows)}")
    sections = [section for row in rows for section in row["sections"]]
    print(f"Sections discovered: {len(sections)}")
    if args.inspect_sections:
        print(f"Sections checked: {sum(section.get('status') == 'checked' for section in sections)}")
        print(f"Upcoming candidates: {sum(len(section.get('upcoming_candidates', ())) for section in sections)}")


if __name__ == "__main__":
    main()
