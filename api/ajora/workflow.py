import json
import re
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher

import httpx
from sqlalchemy import select

from .agents import model_candidates, review_notice
from .event_dates import DEADLINE, verify_dates
from .collect import Collector, PARSER_VERSION, digest, normalized, official, parse
from .db import Change, Event, Evidence, Institute, Notification, Review, Run, Saved, Snapshot, Source, Student, now
from .materials import material_choices, select_materials
from .pricing import requires_price_check, verify_prices
from .registration import PRINTED_URL
from .schema import Candidate, application_url, claim_fields

NOTICE_CHANGE = re.compile(r"\b(?:cancelled|canceled|postponed|rescheduled)\b", re.I)


def today():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def add_review(db, snapshot, kind, proposal, context="", event_id=None, previous_version=None):
    key = digest(json.dumps([snapshot.id, kind, event_id, proposal, previous_version], sort_keys=True))
    if not db.scalar(select(Review.id).where(Review.dedupe_key == key)):
        db.add(Review(snapshot_id=snapshot.id, kind=kind, proposal=proposal,
                      context=context, event_id=event_id, dedupe_key=key))


def event_identity(institute, candidate):
    return digest(f"{institute}|{normalized(candidate.title)}|{candidate.starts}")[:24]


def latest_publication(db, event_id):
    return db.scalar(select(Review).where(Review.event_id == event_id, Review.status == "approved",
                                          Review.kind.in_(("candidate", "change", "document")))
                     .order_by(Review.resolved_at.desc(), Review.id.desc()).limit(1))


def possible_matches(db, institute, snapshot, candidate):
    titles = []
    for event, version in db.execute(select(Event, Change).join(Change).where(
        Event.institute_id == institute.id, Event.demo.is_(False)).order_by(Change.id.desc())):
        if event.id in {item[0] for item in titles}:
            continue
        current = Candidate.model_validate(event.published or version.current)
        similarity = SequenceMatcher(None, normalized(current.title), normalized(candidate.title)).ratio()
        same_registration = bool(candidate.registration_url and candidate.registration_url == current.registration_url)
        previous_snapshot = db.scalar(select(Snapshot).join(Review, Review.snapshot_id == Snapshot.id)
                                      .where(Review.id == version.review_id))
        same_source = previous_snapshot is not None and (
            previous_snapshot.source_id == snapshot.source_id or previous_snapshot.url == snapshot.url
        )
        nearby = abs((current.starts - candidate.starts).days) <= 3
        if same_registration or (similarity >= 0.83 and nearby) or (similarity >= 0.96 and same_source):
            titles.append((event.id, f"{current.title} ({current.starts})"))
    return titles[:8]


def critical_checks(candidate, previous, current):
    checks = []
    if candidate.status in ("cancelled", "postponed"):
        checks.append(f"Event marked {candidate.status}")
    if candidate.registration_deadline and 0 <= (candidate.registration_deadline - current).days <= 3:
        checks.append("Applications close within 3 days")
    if previous:
        for field, description in (("starts", "Start date changed"), ("ends", "End date changed"),
                                   ("registration_deadline", "Application deadline changed"),
                                   ("registration_url", "Application link changed")):
            if getattr(candidate, field) != getattr(previous, field):
                checks.append(description)
        if any(requires_price_check(item) for item in (candidate, previous)) and candidate.attendance != previous.attendance:
            checks.append("Verify changes to fees or attendance options")
    return checks


def stage(db, institute, snapshot, candidate, evidence, applications, materials, agent=None, notice=None):
    data = candidate.model_dump(mode="json")
    event_id = event_identity(institute.id, candidate)
    event = db.get(Event, event_id)
    if not notice and event and event.published == data and (candidate.registration_url or not applications):
        last = latest_publication(db, event_id)
        prices_reviewed = not requires_price_check(candidate) or (last and last.proposal.get("price_checked"))
        materials_reviewed = not materials or (last and "public_materials" in last.proposal and last.proposal.get("materials") == materials)
        if last and last.proposal.get("dates_checked") and prices_reviewed and materials_reviewed:
            event.checked_at = now()
            return
    if not event and (candidate.ends or candidate.starts) < today() and candidate.status == "scheduled":
        return
    matches = possible_matches(db, institute, snapshot, candidate) if not event else []
    version = db.scalar(select(Change.id).where(Change.event_id == event.id).order_by(Change.id.desc()).limit(1)) if event else None
    context = "Compare with " + ", ".join(f"{label} [{key}]" for key, label in matches) if matches else ""
    previous = Candidate.model_validate(event.published) if event and event.published else None
    if not previous and len(matches) == 1:
        matched = db.get(Event, matches[0][0])
        if matched and matched.published and normalized(matched.published["title"]) == normalized(candidate.title):
            previous = Candidate.model_validate(matched.published)
    checks = critical_checks(candidate, previous, today())
    if notice:
        checks.append("AI flagged a possible event schedule change")
    add_review(db, snapshot, "change" if event else "candidate",
               {"candidate": data, "evidence": evidence,
                "application_links": applications,
                "materials": materials,
                "priority": 1 if checks else 0,
                "critical_checks": checks,
                "extraction_agent": agent,
                "schedule_notice": notice,
                "possible_matches": [{"id": key, "label": label} for key, label in matches]},
               context, event.id if event else None, version)


def refresh(db, source, collector, allow_model=False):
    institute = db.get(Institute, source.institute_id)
    if not source.approved or not source.permission_note or not official(source.url, institute.domains):
        raise ValueError("Source is awaiting permission review")
    source.last_checked = now()
    try:
        detail = {}
        latest_run = db.scalar(select(Run).where(Run.source_id == source.id, Run.outcome == "collected")
                               .order_by(Run.id.desc()).limit(1))
        latest = db.get(Snapshot, latest_run.details["snapshot"]) if latest_run else None
        reprocess = not latest_run or latest_run.details.get("parser_version") != PARSER_VERSION or (
            allow_model and not latest_run.details.get("agent_workflow"))
        conditional = not reprocess and latest and latest.kind != "html_bundle"
        body, headers, url, kind = collector.fetch(source.url, source.etag if conditional else None,
                                                   source.modified if conditional else None)
        if body is None or (not reprocess and latest and latest.digest == digest(body)):
            outcome = "unchanged"
        else:
            parsed = parse(body, kind, url, institute.domains)
            links, candidates = parsed.discovered, parsed.candidates
            applications = [asdict(link) for link in parsed.applications]
            extraction_agent = None
            hashed = digest(body)
            snapshot = db.scalar(select(Snapshot).where(Snapshot.source_id == source.id, Snapshot.digest == hashed))
            if not snapshot:
                snapshot = Snapshot(source_id=source.id, digest=hashed, kind=kind, url=url, body=body)
                db.add(snapshot)
                db.flush()
            materials = material_choices(snapshot, institute.domains)
            source_urls = set(db.scalars(select(Source.url).where(Source.institute_id == institute.id)))
            for target, label in links:
                if target not in source_urls:
                    add_review(db, snapshot, "source", {"url": target, "label": label}, "Review terms before enabling crawling")
            missing_deadline = any(not candidate.registration_deadline for candidate, _ in candidates)
            if allow_model and kind not in ("xml", "image") and (not candidates or (missing_deadline and DEADLINE.search(parsed.text))):
                try:
                    suggested, agent = model_candidates(parsed.text, today())
                    extraction_agent = agent
                    detail["extraction_agent"] = agent
                    usable = []
                    for candidate, evidence in suggested:
                        try:
                            verify_dates(candidate, evidence, parsed.candidates)
                        except ValueError:
                            continue
                        if all(normalized(evidence.get(field, "")) in normalized(parsed.text) and evidence.get(field)
                               for field in ("starts", "ends", "registration_deadline") if getattr(candidate, field)):
                            usable.append((candidate, evidence))
                    if not candidates:
                        candidates = usable
                    elif usable:
                        deadlines = {(normalized(item.title), item.starts): (item.registration_deadline, proof["registration_deadline"])
                                     for item, proof in usable if item.registration_deadline}
                        for index, (item, proof) in enumerate(candidates):
                            found = deadlines.get((normalized(item.title), item.starts))
                            if not item.registration_deadline and found and found[0] <= (item.ends or item.starts):
                                candidates[index] = (item.model_copy(update={"registration_deadline": found[0]}),
                                                     proof | {"registration_deadline": found[1]})
                except ValueError:
                    detail["extraction"] = "Provider response could not be used"
            notices = {}
            # Only send notices for schedule review when an institute has a published event.
            if allow_model and kind in ("html", "html_bundle", "pdf") and NOTICE_CHANGE.search(parsed.text):
                upcoming = list(db.scalars(
                    select(Event).where(Event.institute_id == institute.id, Event.status == "published",
                                        Event.demo.is_(False), Event.starts >= today().isoformat())
                    .order_by(Event.starts).limit(30)))
                titles = {item.published["title"]: item.id for item in upcoming}
                if titles:
                    alerts, provider = review_notice(parsed.text, list(titles))
                    detail["schedule_review_agent"] = provider
                    captured_text = normalized(parsed.text)
                    for alert in alerts:
                        if not provider or not isinstance(alert, dict):
                            continue
                        title, change, quote = (alert.get(key) for key in ("title", "change", "excerpt"))
                        if (isinstance(title, str) and title in titles and isinstance(quote, str)
                                and 12 <= len(quote) <= 800 and change in ("cancelled", "postponed", "rescheduled")
                                and normalized(quote) in captured_text and NOTICE_CHANGE.search(quote)):
                            notices[title] = {"change": change, "excerpt": quote, "provider": provider}
                    # A cancellation notice can omit the already-published dates.
                    # Queue it as a document with the existing event ID for human review.
                    candidate_titles = {candidate.title for candidate, _ in candidates}
                    for title in notices.keys() - candidate_titles:
                        add_review(db, snapshot, "document", {
                            "priority": 1, "critical_checks": ["AI flagged a possible event schedule change"],
                            "schedule_notice": notices[title], "application_links": applications, "materials": materials,
                        }, title, titles[title])
            for candidate, evidence in candidates:
                distinct_urls = {link.url for link in parsed.applications
                                 if link.method == "Structured event offer" or (
                                     link.label and not re.search(r"expression of interest|payment|pay fee", link.label, re.I))}
                if not candidate.registration_url and len(candidates) == len(distinct_urls) == 1:
                    candidate = candidate.model_copy(update={"registration_url": next(iter(distinct_urls))})
                stage(db, institute, snapshot, candidate, evidence, applications, materials,
                      extraction_agent, notices.get(candidate.title))
            if not candidates and not notices and kind != "xml":
                add_review(db, snapshot, "document", {"application_links": applications, "materials": materials},
                           "Review the document for academic events")
            detail |= {"candidates": len(candidates), "links": len(links), "application_links": len(applications),
                       "snapshot": snapshot.id, "parser_version": PARSER_VERSION,
                       "agent_workflow": allow_model and "extraction" not in detail
                       and ("schedule_review_agent" not in detail or bool(detail["schedule_review_agent"]))}
            outcome = "collected"
        if body is not None:
            source.etag, source.modified = headers.get("etag"), headers.get("last-modified")
        source.last_success = now()
        source.failures = 0
    except (ValueError, OSError, httpx.HTTPError, RuntimeError) as error:
        source.failures += 1
        outcome, detail = "failed", {"message": str(error)[:300]}
    db.add(Run(source_id=source.id, outcome=outcome, details=detail))
    return outcome


def notify(db, event, key, message, new=False):
    interested = set(db.scalars(select(Saved.student_id).where(Saved.event_id == event.id)))
    if new:
        interested.update(student.id for student in db.scalars(select(Student).where(Student.verified.is_(True)))
                          if event.institute_id in student.preferences.get("institutes", []))
    for student in db.scalars(select(Student).where(Student.id.in_(interested))):
        if student.preferences.get("alerts") and not db.scalar(select(Notification.id).where(
            Notification.student_id == student.id, Notification.key == key
        )):
            db.add(Notification(student_id=student.id, event_id=event.id, key=key, message=message))


def current_snapshot(db, review):
    snapshot = db.get(Snapshot, review.snapshot_id)
    if not snapshot:
        raise ValueError("Publication requires a collected source")
    source = db.get(Source, snapshot.source_id)
    institute = db.get(Institute, source.institute_id)
    if not source.approved or not official(snapshot.url, institute.domains):
        raise ValueError("The snapshot source is no longer approved")
    latest_run = db.scalar(select(Run).where(Run.source_id == source.id, Run.outcome == "collected")
                           .order_by(Run.id.desc()).limit(1))
    if latest_run and latest_run.details.get("snapshot") != snapshot.id:
        raise ValueError("Review the latest collected snapshot")
    return snapshot, source, institute


def application_proof(parsed, registration, domains, confirmed):
    matches = [link for link in parsed.applications if link.url == registration]
    matched = next((link for link in matches if link.method != "QR code"), matches[0] if matches else None)
    is_official = official(registration, domains)
    if not matched and not (is_official and registration in
                            {value.rstrip(".,);]") for value in PRINTED_URL.findall(parsed.text)}):
        raise ValueError("The application URL must appear in the official announcement or its QR code")
    if (not is_official or (matched and matched.method == "QR code")) and not confirmed:
        raise ValueError("Confirm that the QR or external destination is the registration page for this event")
    return f"{matched.method} · {matched.location} · {registration}" if matched else registration


def publish(db, review, candidate, evidence, target_id=None, application_confirmed=False, prices_confirmed=False,
            material_keys=()):
    snapshot, source, institute = current_snapshot(db, review)
    selected_materials = select_materials(snapshot, institute.domains, material_keys)
    parsed = parse(snapshot.body, snapshot.kind, snapshot.url, institute.domains)
    fields = claim_fields(candidate)
    registration = candidate.registration_url
    if registration:
        fields.discard("registration_url")
        excerpt = application_proof(parsed, registration, institute.domains, application_confirmed)
    if fields - evidence.keys():
        raise ValueError("Missing evidence for " + ", ".join(sorted(fields - evidence.keys())))
    content = normalized(parsed.text)
    for field in fields:
        quote = normalized(evidence[field])
        if len(quote) < 2 or quote not in content:
            raise ValueError(f"Evidence for {field} does not appear in the collected source")
    verify_dates(candidate, evidence, parsed.candidates)
    review.proposal = review.proposal | {"dates_checked": True}
    if requires_price_check(candidate):
        verify_prices(candidate, evidence, parsed.candidates)
        if not prices_confirmed:
            raise ValueError("Double-check the institute's fee, certificate cost, deposit and eligibility before publishing")
        review.proposal = review.proposal | {"price_checked": True}
    if registration:
        fields.add("registration_url")
        evidence = evidence | {"registration_url": excerpt}
    review.proposal = review.proposal | {"public_materials": selected_materials,
                                         "materials": material_choices(snapshot, institute.domains)}
    event_id = review.event_id or target_id or event_identity(institute.id, candidate)
    event = db.get(Event, event_id, with_for_update=True)
    if (review.event_id or target_id) and (not event or event.institute_id != institute.id or event.demo):
        raise ValueError("Choose an existing event from this institution")
    if not event and candidate.status != "scheduled":
        raise ValueError("A cancellation or postponement needs an existing event")
    if not event:
        event = Event(id=event_id, institute_id=institute.id, starts=candidate.starts.isoformat())
        db.add(event)
        db.flush()
    previous = event.published or {}
    data = candidate.model_dump(mode="json")
    event.status = "published" if candidate.status == "scheduled" else candidate.status
    event.published = data
    event.starts = candidate.starts.isoformat()
    event.checked_at = source.last_success
    review.event_id = event.id
    for field in sorted(fields):
        db.add(Evidence(event_id=event.id, review_id=review.id, snapshot_id=snapshot.id, field=field, excerpt=evidence[field]))
    if previous != data:
        db.add(Change(event_id=event.id, review_id=review.id, previous=previous, current=data))
        notify(db, event, f"review:{review.id}", f"{candidate.title} was {candidate.status if candidate.status != 'scheduled' else 'updated' if previous else 'published'}.", not previous)
    return event


def supplementary_event(db, institute, target_id):
    event = db.get(Event, target_id, with_for_update=True)
    if not event or event.institute_id != institute.id or event.status != "published" or event.demo or not event.published:
        raise ValueError("Choose a published event from this institution")
    return event


def confirm_schedule_notice(db, review):
    snapshot, _, institute = current_snapshot(db, review)
    event = supplementary_event(db, institute, review.event_id)
    notice = review.proposal.get("schedule_notice") or {}
    change, quote = notice.get("change"), notice.get("excerpt", "")
    if change not in ("cancelled", "postponed") or not quote:
        raise ValueError("Only a verified cancellation or postponement can use a schedule notice")
    text = normalized(parse(snapshot.body, snapshot.kind, snapshot.url, institute.domains).text)
    wording = "cancel(?:l)?ed" if change == "cancelled" else "postponed"
    if (normalized(quote) not in text or normalized(event.published["title"]) not in text
            or not re.search(rf"\b{wording}\b", quote, re.I)):
        raise ValueError("Verify the event title and schedule announcement against the official source")
    previous = event.published
    event.status = change
    event.published = previous | {"status": change}
    review.kind = "schedule_notice"
    db.add(Evidence(event_id=event.id, review_id=review.id, snapshot_id=snapshot.id, field="status", excerpt=quote))
    db.add(Change(event_id=event.id, review_id=review.id, previous=previous, current=event.published))
    notify(db, event, f"review:{review.id}", f"{previous['title']} was {change}.")


def attach_materials(db, review, target_id, material_keys):
    snapshot, _, institute = current_snapshot(db, review)
    event = supplementary_event(db, institute, target_id)
    selected = select_materials(snapshot, institute.domains, material_keys)
    if not selected:
        raise ValueError("Select at least one flyer or poster to attach")
    review.kind, review.event_id = "material", event.id
    review.proposal = review.proposal | {"public_materials": selected}


def attach_application(db, review, target_id, registration, confirmed, material_keys=()):
    if not confirmed:
        raise ValueError("Confirm that this destination is the registration page for the existing event")
    snapshot, _, institute = current_snapshot(db, review)
    event = supplementary_event(db, institute, target_id)
    if not application_url(registration):
        raise ValueError("A public HTTPS registration URL is required")
    parsed = parse(snapshot.body, snapshot.kind, snapshot.url, institute.domains)
    excerpt = application_proof(parsed, registration, institute.domains, confirmed)
    previous = event.published
    event.published = previous | {"registration_url": registration}
    review.kind, review.event_id = "application", event.id
    review.proposal = review.proposal | {"public_materials": select_materials(snapshot, institute.domains, material_keys)}
    db.add(Evidence(event_id=event.id, review_id=review.id, snapshot_id=snapshot.id,
                    field="registration_url", excerpt=excerpt))
    if event.published != previous:
        db.add(Change(event_id=event.id, review_id=review.id, previous=previous, current=event.published))
        notify(db, event, f"review:{review.id}", f"The registration link for {previous['title']} was updated.")


def source_due(source, timestamp, force=False):
    return source.approved and (
        force or not source.last_checked or timestamp >= datetime.fromisoformat(source.last_checked)
        + timedelta(hours=min(168, source.interval_hours * (2 ** min(source.failures, 4))))
    )


def due(db, force=False, limit=12):
    rows = list(db.scalars(select(Source).where(Source.approved.is_(True)).order_by(Source.last_checked.asc().nulls_first())))
    timestamp = datetime.now(timezone.utc)
    selected = [source.id for source in rows if source_due(source, timestamp, force)]
    return selected[:limit]


def deadline_alerts(db):
    for event in db.scalars(select(Event).where(Event.status == "published", Event.demo.is_(False), Event.starts >= today().isoformat())):
        if deadline := event.published.get("registration_deadline"):
            if 0 <= (date.fromisoformat(deadline) - today()).days <= 2:
                notify(db, event, f"deadline:{event.id}:{deadline}", f"Registration for {event.published['title']} closes on {deadline}.")
