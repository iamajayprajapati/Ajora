from datetime import timedelta
from collections import Counter
from base64 import b64encode
from io import BytesIO
from pathlib import Path

import httpx
import pymupdf
import pytest
import zxingcpp
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ajora.collect import Collector, official, parse
from ajora.db import Base, Change, Event, Review, Source, get_db, make_engine
from ajora.main import app
from ajora.seed import seed
from ajora.workflow import refresh, today

ADMIN = {"X-Ajora-Admin": "test-admin-token-long-enough-for-authentication"}


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    make_session = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv("AJORA_ADMIN_TOKEN", ADMIN["X-Ajora-Admin"])
    monkeypatch.setenv("AJORA_LOCAL_DEMO", "1")
    with make_session() as db:
        seed(db, demo=True)
        db.commit()

    def override():
        with make_session() as db:
            yield db

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app), make_session
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_student_search_save_alerts_and_deletion(pilot):
    client, _ = pilot
    assert client.get("/api/institutes").status_code == 200
    found = client.get("/api/events", params={"mode": "online", "max_fee": 0, "certificate": "free", "level": "UG"})
    assert found.json()["total"] == 2
    assert all(row["demo"] for row in found.json()["events"])
    assert client.put("/api/account/saved/sample-1").status_code == 401
    credentials = {"email": "student@example.org", "password": "An example passphrase", "adult": False, "consent": True}
    assert client.post("/api/account/signup", json=credentials).status_code == 400
    assert client.post("/api/account/signup", json=credentials | {"adult": True}).status_code == 201
    assert client.put("/api/account/saved/sample-1").status_code == 200
    assert client.put("/api/account/saved/sample-1").status_code == 200
    assert client.put("/api/account/preferences", json={"alerts": True, "institutes": ["iit-bombay"]}).status_code == 200
    assert len(client.get("/api/account/me").json()["saved"]) == 1
    assert client.post("/api/submissions", json={
        "institute_id": "iit-bombay", "source_url": "https://evil-iitb.ac.in/",
        "notes": "Please review this workshop."
    }).status_code == 400
    assert client.delete("/api/account").status_code == 200
    assert client.get("/api/account/me").status_code == 401


def test_public_feedback_requires_an_account_to_post_or_reply(pilot):
    author, _ = pilot
    visitor = TestClient(app)
    member = TestClient(app)
    topic = {"title": "More online workshop filters", "category": "Suggestion", "body": "An evening filter would help."}
    assert visitor.get("/api/feedback").json()["topics"] == []
    assert visitor.post("/api/feedback", json=topic).status_code == 401
    for client, email in [(author, "author@example.org"), (member, "member@example.org")]:
        signed_up = client.post("/api/account/signup", json={
            "email": email, "password": "An example passphrase", "adult": True, "consent": True,
        })
        assert signed_up.status_code == 201, signed_up.text
    result = author.post("/api/feedback", json=topic)
    assert result.status_code == 201, result.text
    post_id = result.json()["id"]
    path = f"/api/feedback/{post_id}"
    listing = visitor.get("/api/feedback").json()["topics"][0]
    assert listing["title"] == topic["title"]
    assert not listing["can_remove"] and "email" not in listing and "student_id" not in listing
    assert visitor.get(path).json()["topic"]["body"] == topic["body"]
    assert visitor.post(path + "/replies", json={"body": "Guest reply"}).status_code == 401
    assert member.delete(f"/api/feedback/posts/{post_id}").status_code == 404

    reply = member.post(path + "/replies", json={"body": "An evening option would help me too."})
    assert reply.status_code == 201, reply.text
    reply_id = reply.json()["id"]
    assert visitor.get(path).json()["topic"]["reply_count"] == 1
    assert member.get(path).json()["replies"][0]["can_remove"]

    assert author.delete("/api/account").status_code == 200
    after = visitor.get(path).json()
    assert after["topic"]["removed"] and after["topic"]["body"] == ""
    assert after["replies"][0]["body"] == reply.json()["body"]
    assert member.post(path + "/replies", json={"body": "This discussion is closed."}).status_code == 404
    assert member.delete(f"/api/feedback/posts/{reply_id}").status_code == 200
    assert visitor.get(path).status_code == 404


def test_directory_ranking_and_visitor_cookie(pilot):
    client, make_session = pilot
    directory = client.get("/api/institutes").json()
    assert Counter(row["category"] for row in directory) == {"IIT": 23, "NIT": 31, "IIIT": 31, "Central University": 57}
    assert Counter(row["subgroup"] for row in directory if row["category"] == "IIIT") == {
        "Centrally funded": 5, "PPP institutes": 20, "Additional campus": 1, "Other IIITs": 5,
    }
    assert sum(row["sample_events"] for row in directory) == 6
    assert sum(row["upcoming_events"] for row in directory) == 0
    assert all(row["official_url"].startswith("https://") and row["domains"] for row in directory)
    assert client.post("/api/visits").json() == {"total": 1}
    assert client.post("/api/visits").json() == {"total": 1}
    second_visitor = TestClient(app)
    assert second_visitor.post("/api/visits").json() == {"total": 2}

    with make_session() as db:
        assert len(list(db.scalars(select(Source)))) == 20
        event = db.get(Event, "sample-1")
        db.add(Event(id="upcoming", institute_id="iit-dharwad", status="published", starts=event.starts,
                     published=event.published, demo=False))
        past = (today() - timedelta(days=20)).isoformat()
        db.add(Event(id="recent", institute_id="iit-jammu", status="published", starts=past,
                     published=event.published | {"starts": past}, demo=False))
        db.commit()
    iits = [row for row in client.get("/api/institutes").json() if row["category"] == "IIT"]
    assert [row["id"] for row in iits[:2]] == ["iit-dharwad", "iit-jammu"]


def test_n8n_refresh_uses_a_separate_credential_and_queues_full_crawl(pilot, monkeypatch):
    from kombu.exceptions import OperationalError

    client, _ = pilot
    route = "/api/automation/refresh"
    token = "test-automation-token-long-enough"
    refresh_header = {"X-Ajora-Refresh": token}
    monkeypatch.setenv("AJORA_REFRESH_TOKEN", token)
    queued = []

    def queue(name, *, kwargs, retry):
        from types import SimpleNamespace

        queued.append((name, kwargs, retry))
        return SimpleNamespace(id="test-job")

    monkeypatch.setattr("ajora.worker.worker.send_task", queue)
    assert client.post(route, headers=ADMIN).status_code == 401
    assert not queued
    response = client.post(route, headers=refresh_header)
    assert response.status_code == 202
    assert response.json() == {"status": "queued", "job_id": "test-job"}
    assert queued == [("ajora.monitor", {"full": True}, False)]

    def unavailable(*_args, **_kwargs):
        raise OperationalError("Broker offline")

    monkeypatch.setattr("ajora.worker.worker.send_task", unavailable)
    assert client.post(route, headers=refresh_header).status_code == 503


def test_full_refresh_includes_more_than_the_regular_batch(pilot):
    from datetime import datetime, timezone

    from ajora.workflow import due

    _, make_session = pilot
    with make_session.begin() as db:
        urls = [f"https://www.iitb.ac.in/events/{index}" for index in range(15)]
        old = (datetime.now(timezone.utc) - timedelta(hours=26)).isoformat()
        for index, url in enumerate(urls):
            db.add(Source(institute_id="iit-bombay", url=url, approved=True, last_checked=old,
                          failures=1 if index == len(urls) - 1 else 0))
        db.add(Source(institute_id="iit-bombay", url="https://www.iitb.ac.in/disabled"))
        db.flush()
        assert len(due(db)) == 12
        ids = due(db, limit=None)
        assert len(ids) == 14
        assert set(db.scalars(select(Source.url).where(Source.id.in_(ids)))) == set(urls[:-1])


def test_institute_profile_reports_event_source_coverage(pilot):
    client, make_session = pilot
    path = "/api/institutes/iit-bombay"
    assert client.get(path).json()["event_monitor"]["status"] == "pending"
    with make_session.begin() as db:
        source = db.scalar(select(Source).where(Source.institute_id == "iit-bombay"))
        source.approved = True
        source.last_checked = "2026-11-17T08:00:00+00:00"
        source.failures = 1
    assert client.get(path).json()["event_monitor"]["status"] == "unavailable"
    with make_session.begin() as db:
        source = db.scalar(select(Source).where(Source.institute_id == "iit-bombay"))
        source.failures = 0
        source.last_success = source.last_checked
        db.add(Source(institute_id="iit-bombay", url="https://www.iitb.ac.in/announcements", approved=True))
    monitor = client.get(path).json()["event_monitor"]
    assert monitor["status"] == "partial"
    assert monitor["last_success"] == "2026-11-17T08:00:00+00:00"
    with make_session.begin() as db:
        new_source = db.scalar(select(Source).where(Source.url == "https://www.iitb.ac.in/announcements"))
        new_source.last_success = "2026-11-17T09:00:00+00:00"
    monitor = client.get(path).json()["event_monitor"]
    assert monitor["status"] == "checked"
    assert monitor["enabled"] == 2 and monitor["pending"] == 0


def test_institute_profiles_and_logo_assets(pilot):
    client, _ = pilot
    directory = client.get("/api/institutes").json()
    logo_folder = Path(__file__).resolve().parents[2] / "web" / "public" / "institute-logos"
    for row in directory:
        response = client.get(f"/api/institutes/{row['id']}")
        assert response.status_code == 200
        detail = response.json()
        assert detail["name"] == row["name"] and detail["introduction"].strip()
        assert row["logo_url"] == f"/institute-logos/{row['id']}.webp"
        assert detail["logo_source"].startswith(("https://", "http://"))
        with Image.open(logo_folder / f"{row['id']}.webp") as asset:
            assert asset.format == "WEBP"
    assert client.get("/api/institutes/unknown").status_code == 404


def test_python_extracts_brochure_dates_and_keeps_evidence():
    from datetime import date

    import pymupdf

    from ajora.collect import normalized, parse
    from ajora.event_dates import verify_dates

    url = "https://nit.example.edu/brochure.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((60, 60), "Two days workshop on\nSignals and Wireless Systems\n(Hybrid event)\n"
                                  "February 19th 2027 to\nFebruary 20th 2027\n"
                                  "Last date for\nregistration\n7 February 2027")
        parsed = parse(pdf.tobytes(), "pdf", url, ["nit.example.edu"])
    [(event, evidence)] = parsed.candidates
    assert event.title == "Signals and Wireless Systems"
    assert (event.starts, event.ends, event.registration_deadline) == (
        date(2027, 2, 19), date(2027, 2, 20), date(2027, 2, 7),
    )
    verify_dates(event, evidence, [])
    assert all(normalized(quote) in normalized(parsed.text) for quote in evidence.values())


def test_event_dates_separate_the_event_and_application_deadline():
    from ajora.event_dates import dates_in, verify_dates
    from ajora.schema import Candidate

    candidate = Candidate(title="Design workshop", starts="2027-01-19", ends="2027-01-21",
                          registration_deadline="2027-01-05")
    evidence = {"starts": "Workshop dates: 19–21 January 2027", "ends": "Workshop dates: 19–21 January 2027",
                "registration_deadline": "Last date for registration: 05/01/2027"}
    verify_dates(candidate, evidence, [])
    assert len(dates_in("January 19–21, 2027")) == 2
    for quote in ("Last date for registration: 5 January", "Paper deadline: 5 January 2027",
                  "Registration deadline: 9 January 2027", "Registration closes 5 January 2027; Event date 19 January 2027"):
        with pytest.raises(ValueError, match="explicit deadline"):
            verify_dates(candidate, evidence | {"registration_deadline": quote}, [])
    with pytest.raises(ValueError, match="starts"):
        verify_dates(candidate, evidence | {"starts": "Registration closes 19 January 2027"}, [])


def test_extraction_prefers_opted_in_gemini_then_local_fallback(monkeypatch):
    import json
    from ajora.agents import configured_agents, model_candidates

    monkeypatch.setenv("GEMINI_API_KEY", "fixture-not-a-real-key")
    monkeypatch.setenv("AJORA_GEMINI_FREE_TIER", "0")
    monkeypatch.setenv("AJORA_LOCAL_MODEL_URL", "https://metered.example/chat/completions")
    assert configured_agents() == []
    monkeypatch.setenv("AJORA_GEMINI_FREE_TIER", "1")
    monkeypatch.setenv("AJORA_LOCAL_MODEL_URL", "http://127.0.0.1:8080/v1/chat/completions")
    calls = []
    client_constructor = httpx.Client

    def fixture(request):
        calls.append(request.url.host)
        if request.url.host == "generativelanguage.googleapis.com":
            assert "tools" not in json.loads(request.content)
            return httpx.Response(429)
        response = {"events": [{"candidate": {"title": "Design workshop", "starts": "2027-01-19"},
                                "evidence": {"title": "Design workshop", "starts": "19 January 2027"}}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_constructor(transport=httpx.MockTransport(fixture), **kwargs))
    candidates, agent = model_candidates("Design workshop, 19 January 2027", today())
    assert calls == ["generativelanguage.googleapis.com", "127.0.0.1"]
    assert agent == "Hugging Face local model" and candidates[0][0].starts.isoformat() == "2027-01-19"


def test_production_preview_and_email_verification(pilot, monkeypatch):
    client, _ = pilot
    monkeypatch.setenv("AJORA_LOCAL_DEMO", "0")
    received = {}
    monkeypatch.setattr("ajora.auth.verification_email", lambda address, token: received.update(token=token))
    assert client.get("/api/events").json()["total"] == 0
    assert client.get("/api/events/sample-1").status_code == 404
    assert client.get("/api/events?mode=online").status_code == 401
    body = {"email": "reviewer@example.org", "password": "An example passphrase"}
    assert client.post("/api/account/signup", json=body | {"adult": True, "consent": True}).json()["verification_required"]
    assert client.post("/api/account/login", json=body).status_code == 403
    assert client.post("/api/account/verify", json=received).status_code == 200
    assert client.post("/api/account/verify", json=received).status_code == 400
    login = client.post("/api/account/login", json=body)
    assert login.status_code == 200
    assert "Secure" in login.headers["set-cookie"] and "HttpOnly" in login.headers["set-cookie"]


def test_agent_flags_schedule_notice_without_changing_public_event(pilot, monkeypatch):
    client, make_session = pilot
    starts = (today() + timedelta(days=8)).isoformat()
    title = "Verified Robotics Workshop"
    excerpt = f"{title} has been postponed by the organizers."
    monkeypatch.setattr("ajora.workflow.model_candidates", lambda *_: ([], "Test agent"))
    monkeypatch.setattr("ajora.workflow.review_notice", lambda *_: ([
        {"title": title, "change": "postponed", "excerpt": excerpt},
    ], "Test agent"))

    class FakeCollector:
        def fetch(self, url, etag, modified):
            return self.body.encode(), {}, url, "html"

    collector = FakeCollector()
    collector.body = ('<script type="application/ld+json">'
                      f'{{"@type":"Event","name":"{title}","startDate":"{starts}"}}</script>')
    with make_session.begin() as db:
        source = db.scalar(select(Source).where(Source.institute_id == "iit-bombay"))
        source.approved, source.permission_note = True, "Test policy approval"
        assert refresh(db, source, collector) == "collected"
    [draft] = client.get("/api/admin/reviews", headers=ADMIN).json()
    proposal = draft["proposal"]
    approved = client.post(f"/api/admin/reviews/{draft['id']}", headers=ADMIN, json={
        "action": "publish", "note": "Checked the official event", "candidate": proposal["candidate"],
        "evidence": proposal["evidence"],
    })
    event_id = approved.json()["event_id"]
    collector.body = f"<p>{excerpt}</p>"
    with make_session.begin() as db:
        source = db.scalar(select(Source).where(Source.institute_id == "iit-bombay"))
        assert refresh(db, source, collector, allow_model=True) == "collected"
    [notice] = client.get("/api/admin/reviews", headers=ADMIN).json()
    assert notice["event_id"] == event_id
    assert notice["proposal"]["priority"] == 1
    assert notice["proposal"]["schedule_notice"]["excerpt"] == excerpt
    assert client.get(f"/api/events/{event_id}").json()["status"] == "scheduled"
    decision = client.post(f"/api/admin/reviews/{notice['id']}", headers=ADMIN, json={
        "action": "confirm_schedule_notice", "note": "Verified postponement with the official notice",
    })
    assert decision.status_code == 200, decision.text
    assert client.get(f"/api/events/{event_id}").json()["status"] == "postponed"


def test_review_gate_evidence_changes_and_failed_monitoring(pilot):
    client, make_session = pilot
    starts = (today() + timedelta(days=8)).isoformat()
    assert client.get("/api/admin/reviews").status_code == 401

    class FakeCollector:
        status = "EventScheduled"
        outage = False

        def fetch(self, url, etag, modified):
            if self.outage:
                raise ValueError("Missing source")
            body = f'<script type="application/ld+json">{{"@type":"Event","name":"A verified example workshop","startDate":"{starts}","eventStatus":"https://schema.org/{self.status}"}}</script>'
            return body.encode(), {}, url, "html"

    collector = FakeCollector()

    def collect():
        with make_session() as db:
            source = db.scalar(select(Source).where(Source.institute_id == "iit-bombay"))
            source.approved = True
            source.permission_note = "Test source policy"
            result = refresh(db, source, collector)
            db.commit()
            return result

    assert collect() == "collected"
    assert collect() == "unchanged"
    reviews = client.get("/api/admin/reviews", headers=ADMIN).json()
    assert len(reviews) == 1
    assert not client.get("/api/events?q=verified").json()["events"]
    first = reviews[0]
    proposal = first["proposal"]
    url = f"/api/admin/reviews/{first['id']}"
    decision = {"action": "publish", "candidate": proposal["candidate"], "evidence": proposal["evidence"], "note": "Checked the source snapshot"}
    bad = decision | {"evidence": proposal["evidence"] | {"title": "fabricated excerpt"}}
    assert client.post(url, json=bad, headers=ADMIN).status_code == 400
    result = client.post(url, json=decision, headers=ADMIN)
    assert result.status_code == 200, result.text
    published_id = result.json()["event_id"]
    listing = client.get(f"/api/events/{published_id}").json()
    assert listing["verification"] == "verified" and listing["new"] is True
    assert client.post(url, json=decision, headers=ADMIN).status_code == 404
    assert client.post("/api/account/signup", json={
        "email": "watcher@example.org", "password": "An example passphrase", "adult": True, "consent": True
    }).status_code == 201
    assert client.put("/api/account/preferences", json={"alerts": True, "institutes": []}).status_code == 200
    assert client.put(f"/api/account/saved/{published_id}").status_code == 200

    collector.outage = True
    assert collect() == "failed"
    listing = client.get(f"/api/events/{published_id}").json()
    assert listing["status"] == "scheduled" and listing["verification"] == "stale"
    collector.outage = False
    collector.status = "EventCancelled"
    assert collect() == "collected"
    assert client.get(f"/api/events/{published_id}").json()["status"] == "scheduled"
    change = client.get("/api/admin/reviews", headers=ADMIN).json()[0]
    decision |= {key: change["proposal"][key] for key in ("candidate", "evidence")}
    assert client.post(f"/api/admin/reviews/{change['id']}", json=decision, headers=ADMIN).status_code == 200
    assert client.get(f"/api/events/{published_id}").json()["status"] == "cancelled"
    assert not client.get("/api/events?q=verified").json()["events"]
    alerts = client.get("/api/account/me").json()["notifications"]
    assert len(alerts) == 1 and "cancelled" in alerts[0]["message"]
    with make_session() as db:
        assert len(list(db.scalars(select(Change)))) == 2
        assert db.get(Event, published_id).status == "cancelled"
        assert db.get(Review, change["id"]).event_id == published_id


def test_collector_respects_robots_and_domain_boundaries(monkeypatch):
    calls = []
    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 443))])

    def fixture(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(302, headers={"Location": "/home/robots.txt"})
        if request.url.path == "/home/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /hidden")
        return httpx.Response(302, headers={"Location": "https://not-iitb.ac.in/page"})

    client = httpx.Client(transport=httpx.MockTransport(fixture))
    collector = Collector(["iitb.ac.in"], client=client, min_interval=0)
    with pytest.raises(ValueError, match="robots"):
        collector.fetch("https://www.iitb.ac.in/hidden")
    assert calls == ["/robots.txt", "/home/robots.txt"]
    with pytest.raises(ValueError, match="Redirect"):
        collector.fetch("https://www.iitb.ac.in/events")
    assert calls == ["/robots.txt", "/home/robots.txt", "/events"]
    assert official("https://events.iitb.ac.in/list", ["iitb.ac.in"])
    assert not official("https://iitb.ac.in@evil.example", ["iitb.ac.in"])


def test_structured_fee_does_not_infer_certificate_or_hybrid_price():
    def fixture(mode):
        return f"""<script type="application/ld+json">{{
          "@type": "Event", "name": "Probabilistic Models Workshop", "startDate": "2027-01-18",
          "eventAttendanceMode": "https://schema.org/{mode}EventAttendanceMode",
          "offers": {{"@type": "Offer", "price": "0", "priceCurrency": "INR", "url": "https://unrelated.example"}},
          "organizer": {{"@type": "Organization", "name": "Data Science Department"}}
        }}</script>""".encode()

    parsed = parse(fixture("Online"), "html", "https://www.iitb.ac.in/events", ["iitb.ac.in"])
    candidate, evidence = parsed.candidates[0]
    assert candidate.attendance[0].fee_inr == 0
    assert candidate.attendance[0].certificate_fee_inr is None
    assert candidate.registration_url is None
    assert evidence["attendance.0.fee_inr"] in parsed.text
    assert candidate.entities[0].name == "Data Science Department"
    hybrid = parse(fixture("Mixed"), "html", "https://www.iitb.ac.in/events", ["iitb.ac.in"])
    assert hybrid.candidates[0][0].attendance[0].fee_inr is None


def qr_png(value):
    barcode = zxingcpp.create_barcode(value, zxingcpp.BarcodeFormat.QRCode)
    pixels = memoryview(zxingcpp.write_barcode_to_image(barcode, scale=8))
    height, width = pixels.shape
    output = BytesIO()
    Image.frombytes("L", (width, height), pixels.tobytes()).save(output, format="PNG")
    return output.getvalue()


def test_application_links_from_html_and_attached_poster(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 443))])
    form = "https://forms.gle/ajoraFixture"
    poster = qr_png(form)
    html = b"""<a href="/register?event=20&amp;student=1">Register now</a>
              <a href="javascript:void(0)">Apply</a>
              <img src="/poster.png" alt="Event QR code">
              <img src="https://outside.example/qr.png" alt="External QR code">"""
    calls = []

    def respond(request):
        assert request.url.host == "www.iitb.ac.in"
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if request.url.path == "/poster.png":
            return httpx.Response(200, content=poster, headers={"content-type": "image/png"})
        return httpx.Response(200, content=html, headers={"content-type": "text/html"})

    collector = Collector(["iitb.ac.in"], httpx.Client(transport=httpx.MockTransport(respond)), min_interval=0)
    source = "https://www.iitb.ac.in/workshop"
    body, _, _, kind = collector.fetch(source)
    assert kind == "html_bundle" and calls == ["/robots.txt", "/workshop", "/poster.png"]
    parsed = parse(body, kind, source, ["iitb.ac.in"])
    assert {(item.method, item.url) for item in parsed.applications} == {
        ("Announcement hyperlink", "https://www.iitb.ac.in/register?event=20&student=1"), ("QR code", form),
    }
    assert not parse(qr_png("upi://pay?pa=someone@example.org"), "image", source, ["iitb.ac.in"]).applications


def test_review_publishes_pdf_qr_and_can_attach_a_separate_poster(pilot):
    client, make_session = pilot
    starts = (today() + timedelta(days=12)).isoformat()
    title = "Official Data Science Workshop"
    form = "https://forms.gle/dataScienceFixture"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((40, 40), title)
        page.insert_text((40, 60), starts)
        page.insert_image(pymupdf.Rect(40, 90, 190, 240), stream=qr_png(form))
        brochure = document.tobytes()

    class FixtureCollector:
        def __init__(self, body, kind):
            self.body, self.kind = body, kind

        def fetch(self, url, *_):
            return self.body, {}, url, self.kind

    brochure_url = "https://www.iitb.ac.in/data-science-workshop.pdf"
    with make_session() as db:
        source = Source(institute_id="iit-bombay", url=brochure_url, approved=True, permission_note="Approved fixture")
        db.add(source)
        db.flush()
        assert refresh(db, source, FixtureCollector(brochure, "pdf")) == "collected"
        db.commit()

    review = client.get("/api/admin/reviews", headers=ADMIN).json()[0]
    assert review["proposal"]["application_links"][0] == {
        "url": form, "method": "QR code", "location": "PDF page 1", "label": ""}
    review_url = f"/api/admin/reviews/{review['id']}"
    candidate = {"title": title, "starts": starts, "registration_url": form}
    body = {"action": "publish", "candidate": candidate, "evidence": {"title": title, "starts": starts},
            "note": "Checked registration destination"}
    assert client.post(review_url, headers=ADMIN, json=body).status_code == 400
    fabricated = body | {"candidate": candidate | {"registration_url": "https://www.iitb.ac.in/fabricated"},
                         "application_confirmed": True}
    assert client.post(review_url, headers=ADMIN, json=fabricated).status_code == 400
    publication = client.post(review_url, headers=ADMIN, json=body | {"application_confirmed": True})
    assert publication.status_code == 200, publication.text
    event_id = publication.json()["event_id"]
    event_url = f"/api/events/{event_id}"
    listing = client.get(event_url).json()
    assert (listing["source_url"], listing["registration_url"], listing["registration_external"]) == (brochure_url, form, True)
    assert listing["materials"] == [{"label": "Official flyer or brochure", "kind": "pdf",
                                     "official_url": brochure_url, "preview_url": None}]
    assert any("PDF page 1" in item["excerpt"] for item in listing["evidence"])

    updated_form = "https://www.iitb.ac.in/new-form"
    poster_url = "https://www.iitb.ac.in/revised-workshop-poster.png"
    with make_session() as db:
        source = Source(institute_id="iit-bombay", url=poster_url, approved=True, permission_note="Approved fixture")
        db.add(source)
        db.flush()
        assert refresh(db, source, FixtureCollector(qr_png(updated_form), "image")) == "collected"
        db.commit()
    poster_review = client.get("/api/admin/reviews", headers=ADMIN).json()[0]
    attach = {"action": "attach_application", "target_id": event_id,
              "registration_url": updated_form, "application_confirmed": True, "note": "Checked revised poster and form"}
    result = client.post(f"/api/admin/reviews/{poster_review['id']}", headers=ADMIN, json=attach)
    assert result.status_code == 200, result.text
    listing = client.get(event_url).json()
    assert listing["source_url"] == brochure_url
    assert listing["registration_url"] == updated_form and not listing["registration_external"]
    assert any(poster_url in item["excerpt"] for item in listing["evidence"])
    assert {item["official_url"] for item in listing["materials"]} == {brochure_url, poster_url}
    profile_events = client.get("/api/institutes/iit-bombay/events").json()["events"]
    card = next(item for item in profile_events if item["id"] == event_id)
    assert card["materials"] == listing["materials"]
    assert card["registration_url"] == updated_form and "evidence" not in card
    assert event_id not in {item["id"] for item in client.get("/api/institutes/iit-delhi/events").json()["events"]}
    poster = next(item for item in listing["materials"] if item["kind"] == "image")
    response = client.get(poster["preview_url"])
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.content == qr_png(updated_form)
    assert client.get(poster["preview_url"].replace(event_id, "sample-1")).status_code == 404


def test_public_institute_events_include_every_page_and_archive(pilot, monkeypatch):
    client, make_session = pilot
    monkeypatch.setenv("AJORA_LOCAL_DEMO", "0")
    with make_session() as db:
        for index in range(5):
            starts = (today() + timedelta(days=index - 2)).isoformat()
            payload = {"title": f"Institute workshop {index}", "starts": starts}
            if index == 1:
                payload["ends"] = (today() + timedelta(days=1)).isoformat()
            db.add(Event(id=f"fixture-{index}", institute_id="iit-bombay", status="published",
                         starts=starts, published=payload))
        db.commit()
    url = "/api/institutes/iit-bombay/events"
    first = client.get(url, params={"limit": 3}).json()
    assert [item["id"] for item in first["events"]] == ["fixture-1", "fixture-2", "fixture-3"]
    assert first["has_more"]
    next_page = client.get(url, params={"limit": 3, "offset": 3}).json()
    assert [item["id"] for item in next_page["events"]] == ["fixture-4"]
    assert not next_page["has_more"]
    archive = client.get(url, params={"archive": True}).json()
    assert [item["id"] for item in archive["events"]] == ["fixture-0"]


def test_only_selected_institute_materials_are_public(pilot):
    client, make_session = pilot
    starts = (today() + timedelta(days=12)).isoformat()
    title = "Networks Seminar"
    image = qr_png("https://forms.gle/posterFixture")
    fixture = f"""<h1>{title}</h1><p>{starts}</p>
      <a href="/networks-flyer.pdf">Networks flyer</a><a href="/other-poster.pdf">Other event poster</a>
      <a href="https://elsewhere.example/flyer.pdf">Unofficial flyer</a>
      <img alt="Event poster" src="data:image/png;base64,{b64encode(image).decode()}">""".encode()

    class FixtureCollector:
        def fetch(self, url, *_):
            return fixture, {}, url, "html"

    with make_session() as db:
        source = db.scalar(select(Source).where(Source.institute_id == "iit-bombay"))
        source.approved, source.permission_note = True, "Approved test material"
        assert refresh(db, source, FixtureCollector()) == "collected"
        db.commit()
    review = next(item for item in client.get("/api/admin/reviews", headers=ADMIN).json() if item["kind"] == "document")
    choices = review["proposal"]["materials"]
    assert {item["label"] for item in choices} == {"Networks flyer", "Other event poster", "Embedded poster image"}
    candidate = {"title": title, "starts": starts}
    body = {"action": "publish", "candidate": candidate, "evidence": candidate,
            "note": "Validated the selected seminar flyer and poster"}
    endpoint = f"/api/admin/reviews/{review['id']}"
    assert client.post(endpoint, headers=ADMIN, json=body | {"material_keys": ["arbitrary"]}).status_code == 400
    result = client.post(endpoint, headers=ADMIN, json=body | {"material_keys": [
        item["key"] for item in choices if item["label"] != "Other event poster"
    ]})
    assert result.status_code == 200, result.text
    listing = client.get("/api/events/" + result.json()["event_id"]).json()
    assert {item["label"] for item in listing["materials"]} == {"Networks flyer", "Embedded poster image"}
    poster = next(item for item in listing["materials"] if item["preview_url"])
    assert client.get(poster["preview_url"]).content == image

    with make_session() as db:
        extra = Source(institute_id="iit-bombay", url="https://www.iitb.ac.in/networks-poster.png",
                       approved=True, permission_note="Approved test poster")
        db.add(extra)
        db.flush()
        class PosterCollector:
            def fetch(self, url, *_):
                return image, {}, url, "image"
        assert refresh(db, extra, PosterCollector()) == "collected"
        db.commit()
    extra_review = next(item for item in client.get("/api/admin/reviews", headers=ADMIN).json() if item["kind"] == "document")
    approved = client.post(f"/api/admin/reviews/{extra_review['id']}", headers=ADMIN, json={
        "action": "attach_materials", "target_id": result.json()["event_id"], "note": "Confirmed the standalone poster"
    })
    assert approved.status_code == 200, approved.text
    updated = client.get("/api/events/" + result.json()["event_id"]).json()
    assert updated["source_url"] == listing["source_url"] and updated["registration_url"] is None
    assert any(item["official_url"] == "https://www.iitb.ac.in/networks-poster.png" for item in updated["materials"])


def test_fee_evidence_and_reviewer_checks_gate_free_filters(pilot):
    client, make_session = pilot
    title = "Data Analysis Workshop"
    starts = (today() + timedelta(days=14)).isoformat()
    fixture = f"""<h1>{title}</h1><p>{starts}</p><p>Online for UG students.</p>
        <p>No registration fee.</p><p>Certificate provided. Certificate fee: Rs. 150.</p>
        <p>Refundable deposit: ₹300.</p><p>Registration fee: ₹700.</p>""".encode()

    class FixtureCollector:
        def fetch(self, url, *_):
            return fixture, {}, url, "html"

    with make_session() as db:
        source = db.scalar(select(Source).where(Source.institute_id == "iit-bombay"))
        source.approved, source.permission_note = True, "Approved test announcement"
        assert refresh(db, source, FixtureCollector()) == "collected"
        db.commit()
    review = client.get("/api/admin/reviews", headers=ADMIN).json()[0]
    url = f"/api/admin/reviews/{review['id']}"
    candidate = {"title": title, "starts": starts, "attendance": [{
        "mode": "online", "levels": ["UG"], "fee_inr": 0, "deposit_inr": 300,
        "certificate": "provided", "certificate_fee_inr": 150,
    }]}
    evidence = {
        "title": title, "starts": starts, "attendance.0.mode": "Online for UG students",
        "attendance.0.levels": "Online for UG students", "attendance.0.fee_inr": "No registration fee",
        "attendance.0.deposit_inr": "Refundable deposit: ₹300", "attendance.0.certificate": "Certificate provided",
        "attendance.0.certificate_fee_inr": "Certificate fee: Rs. 150",
    }
    decision = {"action": "publish", "note": "Compared the workshop fee and deposit",
                "candidate": candidate, "evidence": evidence}
    assert client.post(url, headers=ADMIN, json=decision).status_code == 400
    for unsupported in ("Registration fee: ₹700", "Certificate provided", "Refundable deposit: ₹300"):
        result = client.post(url, headers=ADMIN, json=decision | {
            "evidence": evidence | {"attendance.0.fee_inr": unsupported}, "prices_confirmed": True})
        assert result.status_code == 400 and "explicit" in result.json()["detail"]

    result = client.post(url, headers=ADMIN, json=decision | {"prices_confirmed": True})
    assert result.status_code == 200, result.text
    event_id = result.json()["event_id"]
    query = {"q": title, "max_fee": 0}
    assert client.get("/api/events", params=query).json()["total"] == 0
    assert client.get("/api/events", params=query | {"max_fee": 300}).json()["total"] == 1
    assert client.get("/api/events/" + event_id).json()["attendance"][0]["certificate_fee_inr"] == 150

    with make_session() as db:
        old = db.get(Review, review["id"])
        old.proposal = {key: value for key, value in old.proposal.items() if key != "price_checked"}
        db.commit()
    legacy = client.get("/api/events/" + event_id).json()
    assert legacy["price_review_pending"] and legacy["attendance"][0]["fee_inr"] is None
    assert client.get("/api/events", params=query | {"max_fee": 300}).json()["total"] == 0


def test_free_evidence_distinguishes_entry_from_certificates_and_deposits():
    from ajora.pricing import supports_amount

    assert supports_amount("fee_inr", 0, "Participation is free")
    assert supports_amount("fee_inr", 1200, "Registration fee: INR 1,200")
    assert supports_amount("certificate_fee_inr", 0, "Certificate included at no extra cost")
    assert supports_amount("deposit_inr", 100, "Refundable deposit 100/-")
    assert not supports_amount("fee_inr", 0, "Free certificate")
    assert not supports_amount("fee_inr", 0, "No registration fee. Refundable deposit: ₹300")
    assert not supports_amount("fee_inr", 0, "Registration is not free")
    assert not supports_amount("fee_inr", 500, "Registration fee ₹500 / ₹700")
