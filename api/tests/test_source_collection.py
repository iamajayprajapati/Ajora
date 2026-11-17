from datetime import date

import httpx
import pytest
from sqlalchemy import select

from ajora.collect import Collector, normalized, parse
from ajora.db import Base, Source, make_engine
from ajora.discover import queue_sections, section_links
from ajora.event_dates import verify_dates
from ajora.seed import seed
from sqlalchemy.orm import Session


def test_discovery_queues_only_unique_official_sections(tmp_path):
    homepage = "https://www.iitb.ac.in/"
    links = section_links(b"""
        <a href="/campus-news">Campus News</a>
        <a href="/campus-news#top">News</a>
        <a href="/Home/AllEvents/">See what's on</a>
        <a href="/item/123">Research workshop news</a>
        <a href="/notice.pdf">Announcements</a>
        <a href="https://iitb.ac.in.example.org/events">Events</a>
    """, homepage, ["iitb.ac.in"])
    assert [item["url"] for item in links] == [
        homepage + "campus-news", homepage + "Home/AllEvents/",
    ]
    engine = make_engine(f"sqlite:///{tmp_path / 'discovery.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            seed(db)
            report = [{"institute_id": "iit-bombay", "sections": links + [
                {"url": "https://another-institute.example/events"},
            ]}]
            assert queue_sections(db, report) == 2
            assert queue_sections(db, report) == 0
            queued = list(db.scalars(select(Source).where(Source.url.in_([item["url"] for item in links]))))
            assert len(queued) == 2
            assert all(item.institute_id == "iit-bombay" and not item.approved for item in queued)
    finally:
        engine.dispose()


def test_calendar_keeps_titles_and_dates_in_the_same_row():
    parsed = parse(b"""
        <table>
          <tr class="marquee-row"><td>18/12/2026</td><td>20/12/2026</td>
            <td class="views-field-title">Robotics Workshop</td></tr>
          <tr class="marquee-row"><td>02/01/2027</td><td>04/01/2027</td>
            <td class="views-field-title">Wireless Conference</td></tr>
          <tr class="marquee-row"><td>03/01/2027</td><td>01/01/2027</td>
            <td class="views-field-title">Reversed dates workshop</td></tr>
        </table>
    """, "html", "https://www.jnu.ac.in/jnuevents/", ["jnu.ac.in"])
    assert [(event.title, event.starts, event.ends) for event, _ in parsed.candidates] == [
        ("Robotics Workshop", date(2026, 12, 18), date(2026, 12, 20)),
        ("Wireless Conference", date(2027, 1, 2), date(2027, 1, 4)),
    ]
    for event, evidence in parsed.candidates:
        verify_dates(event, evidence, [])
        assert all(normalized(excerpt) in normalized(parsed.text) for excerpt in evidence.values())


@pytest.mark.parametrize("body", [
    b"<rss><channel>",
    b'<!DOCTYPE rss [<!ENTITY name SYSTEM "https://example.org/secret">]><rss>&name;</rss>',
])
def test_unusable_xml_reports_a_source_error(body):
    with pytest.raises(ValueError, match="Invalid or unsupported XML feed"):
        parse(body, "xml", "https://www.iitb.ac.in/events.xml", ["iitb.ac.in"])


def test_crawler_does_not_follow_official_policy_redirect_to_another_domain(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *_args, **_kwargs: [
        (None, None, None, None, ("8.8.8.8", 443)),
    ])
    requested = []

    def response(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://other.example/robots.txt"})

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        collector = Collector(["iitb.ac.in"], client=client, min_interval=0)
        with pytest.raises(ValueError, match="policy redirect left"):
            collector.fetch("https://www.iitb.ac.in/events")
    assert requested == ["https://www.iitb.ac.in/robots.txt"]
