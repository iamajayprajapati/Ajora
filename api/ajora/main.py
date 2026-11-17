import os
import secrets
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from kombu.exceptions import OperationalError
from pydantic import Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import auth, feedback
from .agents import configured_agents
from .collect import official, parse
from .db import Base, Change, Event, Evidence, Institute, Notification, Review, Run, Saved, SessionLocal, SiteCounter, Snapshot, Source, engine, get_db, now
from .materials import image_mime, material_choices, published_materials
from .pricing import PRICE_FIELDS, requires_price_check
from .registry import DIRECTORY, IDENTITY, WEBSITE_CHECKS, current_website, introduction
from .schema import Attendance, Candidate, Level, Mode, Strict
from .seed import seed
from .workflow import attach_application, attach_materials, confirm_schedule_notice, latest_publication, publish, today
from .worker import enqueue_daily_refresh


@asynccontextmanager
async def lifespan(_):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db, demo=auth.local_mode())
        db.commit()
    yield


app = FastAPI(title="AJORA", version="0.1.0", lifespan=lifespan)
app.include_router(auth.router, prefix="/api")
app.include_router(feedback.router, prefix="/api")


@app.middleware("http")
async def request_policy(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        allowed = {os.getenv("AJORA_PUBLIC_URL", "http://localhost:3000").rstrip("/")}
        if auth.local_mode():
            allowed.add("http://127.0.0.1:3000")
        if origin and origin not in allowed:
            return JSONResponse({"detail": "Request origin is invalid"}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "demo": auth.local_mode()}


@app.post("/api/visits")
def visits(request: Request, response: Response, db: Session = Depends(get_db)):
    if request.cookies.get("ajora_counted") == "1":
        return {"total": db.get(SiteCounter, "visitors").total}
    total = db.scalar(update(SiteCounter).where(SiteCounter.name == "visitors")
                      .values(total=SiteCounter.total + 1).returning(SiteCounter.total))
    db.commit()
    response.set_cookie("ajora_counted", "1", max_age=365 * 86400, httponly=True,
                        samesite="lax", secure=not auth.local_mode())
    return {"total": total}


def institute_data(institute):
    data = {key: getattr(institute, key) for key in ("id", "name", "category", "city", "state", "domains", "registry_url")}
    if entry := DIRECTORY.get(institute.id):
        data.update(official_url=current_website(entry), subgroup=entry.subgroup)
        identity = IDENTITY.get(institute.id, {})
        if identity.get("logo_source"):
            data["logo_url"] = f"/institute-logos/{entry.id}.webp"
    return data


@app.get("/api/institutes/{institute_id}")
def institute_detail(institute_id: str, db: Session = Depends(get_db)):
    institute = db.get(Institute, institute_id)
    if not institute:
        raise HTTPException(404, "Institute not found")
    entry = DIRECTORY.get(institute_id)
    identity = IDENTITY.get(institute_id, {})
    sources = list(db.scalars(select(Source).where(Source.institute_id == institute_id)))
    enabled = [source for source in sources if source.approved]
    failed = sum(source.failures > 0 for source in enabled)
    successful = [source.last_success for source in enabled if source.last_success]
    status = ("pending" if not enabled else "unavailable" if failed == len(enabled)
              else "partial" if failed or len(successful) < len(enabled) and successful
              else "checked" if successful else "pending")
    return institute_data(institute) | {
        "introduction": identity.get("introduction") or (introduction(entry) if entry else None),
        "about_url": identity.get("about_url"), "logo_source": identity.get("logo_source"),
        "website_status": WEBSITE_CHECKS.get(institute_id, {}).get("status"),
        "website_checked_on": WEBSITE_CHECKS.get(institute_id, {}).get("checked_on"),
        "event_monitor": {
            "status": status, "enabled": len(enabled), "pending": len(sources) - len(enabled), "failed": failed,
            "last_success": max(successful, default=None),
            "last_checked": max((source.last_checked for source in enabled if source.last_checked), default=None),
        },
    }


@app.get("/api/institutes")
def institutes(db: Session = Depends(get_db)):
    current = today()
    recent_cutoff = current - timedelta(days=365)
    upcoming, samples, recent = Counter(), Counter(), Counter()
    query = select(Event).where(Event.status == "published")
    for event in db.scalars(query):
        end = date.fromisoformat(event.published.get("ends") or event.starts)
        if event.demo:
            if auth.local_mode() and end >= current:
                samples[event.institute_id] += 1
        elif end >= current:
            upcoming[event.institute_id] += 1
        elif end >= recent_cutoff:
            recent[event.institute_id] += 1
    rows = list(db.scalars(select(Institute)))
    rows.sort(key=lambda row: (-upcoming[row.id], -recent[row.id], -samples[row.id], row.name.casefold()))
    return [institute_data(row) | {"upcoming_events": upcoming[row.id], "recent_events": recent[row.id],
                                  "sample_events": samples[row.id]} for row in rows]


def public_candidate(event, reviewed):
    payload = Candidate.model_validate(event.published)
    pending = not event.demo and requires_price_check(payload) and not (reviewed and reviewed.proposal.get("price_checked"))
    if pending:
        payload = payload.model_copy(update={"attendance": [
            option.model_copy(update=dict.fromkeys(PRICE_FIELDS)) for option in payload.attendance
        ]})
    return payload, pending


def event_data(db, event, institute, *, detailed=False, include_materials=False):
    include_materials = include_materials or detailed
    reviewed = latest_publication(db, event.id) if not event.demo else None
    payload, pending = public_candidate(event, reviewed)
    data = {"id": event.id, "institute": institute_data(institute), "demo": event.demo,
            "checked_at": event.checked_at, **payload.model_dump(mode="json")}
    data["price_review_pending"] = pending
    data["date_review_pending"] = not event.demo and not (reviewed and reviewed.proposal.get("dates_checked"))
    if event.demo:
        data["verification"] = "sample"
        if include_materials:
            data["materials"] = []
    else:
        first_published = db.scalar(select(Change.created_at).where(Change.event_id == event.id)
                                    .order_by(Change.id).limit(1))
        data["new"] = bool(first_published and datetime.now(timezone.utc) - datetime.fromisoformat(first_published) < timedelta(days=7))
        snapshot = db.get(Snapshot, reviewed.snapshot_id) if reviewed else None
        source = db.get(Source, snapshot.source_id) if snapshot else None
        data["source_url"] = snapshot.url if snapshot else None
        if payload.registration_url:
            data["registration_external"] = not official(payload.registration_url, institute.domains)
        latest = db.scalar(select(Run).where(Run.source_id == source.id, Run.outcome == "collected")
                           .order_by(Run.id.desc()).limit(1)) if source else None
        changed_since_check = latest and latest.details.get("snapshot") != snapshot.id and (
            not event.checked_at or latest.created_at >= event.checked_at)
        stale = bool(source and (not source.approved or source.failures or not source.last_success or
                       datetime.now(timezone.utc) - datetime.fromisoformat(source.last_success) >
                       timedelta(hours=source.interval_hours * 2) or changed_since_check))
        data["verification"] = "stale" if stale else "verified"
        if include_materials:
            material_base = f"/api/events/{event.id}/materials"
            data["materials"] = [
                {"label": item.label, "kind": item.kind, "official_url": item.url,
                 "preview_url": f"{material_base}/{snapshot_id}/{item.key}" if item.image else None}
                for snapshot_id, item in published_materials(db, event.id, reviewed, institute.domains)
            ]
        if detailed and reviewed:
            data["evidence"] = [{"field": row.field, "excerpt": row.excerpt} for row in
                                db.scalars(select(Evidence).where(Evidence.review_id == reviewed.id,
                                                                 Evidence.field != "registration_url").order_by(Evidence.field))
                                if not pending or row.field.rsplit(".", 1)[-1] not in PRICE_FIELDS]
            if payload.registration_url:
                proof = db.scalar(select(Evidence).where(Evidence.event_id == event.id, Evidence.field == "registration_url")
                                  .order_by(Evidence.id.desc()).limit(1))
                if proof:
                    data["evidence"].append({"field": proof.field, "excerpt": proof.excerpt})
    return data


def public_rows(db):
    query = select(Event, Institute).join(Institute, Event.institute_id == Institute.id).where(
        Event.status.in_(("published", "cancelled", "postponed")), Event.published.is_not(None))
    if not auth.local_mode():
        query = query.where(Event.demo.is_(False))
    return query


@app.get("/api/institutes/{institute_id}/events")
def institute_events(institute_id: str, archive: bool = False,
                     limit: int = Query(12, ge=1, le=24), offset: int = Query(0, ge=0, le=1_000_000),
                     db: Session = Depends(get_db)):
    if not db.get(Institute, institute_id):
        raise HTTPException(404, "Institute not found")
    end = func.coalesce(Event.published["ends"].as_string(), Event.starts)
    current = (Event.status == "published") & (end >= today().isoformat())
    query = public_rows(db).where(Event.institute_id == institute_id, ~current if archive else current)
    order = (Event.starts.desc(), Event.id.desc()) if archive else (Event.starts.asc(), Event.id.asc())
    all_count = db.scalar(select(func.count()).select_from(query.subquery()))
    published_count = db.scalar(select(func.count()).select_from(query.where(Event.demo.is_(False)).subquery()))
    rows = db.execute(query.order_by(*order).offset(offset).limit(limit + 1)).all()
    return {"events": [event_data(db, event, institute, include_materials=True)
                       for event, institute in rows[:limit]], "has_more": len(rows) > limit,
            "total": all_count, "published_count": published_count}


def option_matches(option: Attendance, mode, level, max_fee, certificate):
    if (mode and option.mode != mode) or (level and level not in option.levels):
        return False
    if max_fee is not None and (option.fee_inr is None or option.fee_inr + (option.deposit_inr or 0) > max_fee):
        return False
    if certificate == "free":
        return option.certificate == "provided" and option.certificate_fee_inr == 0
    if certificate == "available":
        return option.certificate == "provided"
    return True


@app.get("/api/events")
def events(q: str = Query("", max_length=120), institute: str = Query("", max_length=100),
           category: str = Query("", max_length=50), mode: Mode | None = None, level: Level | None = None,
           max_fee: int | None = Query(None, ge=0, le=10_000_000),
           certificate: Literal["free", "available"] | None = None, before: date | None = None,
           limit: int = Query(24, ge=1, le=60), offset: int = Query(0, ge=0),
           student=Depends(auth.optional_student), db: Session = Depends(get_db)):
    if not student and not auth.local_mode() and (offset or any([q, category, mode, level, max_fee is not None, certificate, before])):
        raise HTTPException(401, "Sign in to use the full event finder")
    query = public_rows(db).where(Event.status == "published").order_by(Event.starts, Event.id)
    if institute:
        query = query.where((Institute.category == institute) | (Institute.id == institute))
    matches = []
    current_day = today()
    for event, host in db.execute(query):
        data = Candidate.model_validate(event.published)
        if not event.demo and (data.ends or data.starts) < current_day:
            continue
        if (before and data.starts > before) or (category and data.category != category):
            continue
        if q and not all(term.casefold() in " ".join(
                [data.title, host.name, data.description or "", data.category, *data.subject]).casefold() for term in q.split()):
            continue
        if any([mode, level, max_fee is not None, certificate]):
            if (max_fee is not None or certificate == "free") and not event.demo and requires_price_check(data):
                data, _ = public_candidate(event, latest_publication(db, event.id))
            if not any(option_matches(option, mode, level, max_fee, certificate) for option in data.attendance):
                continue
        matches.append((event, host))
    preview = not student and not auth.local_mode()
    if preview:
        matches = matches[:3]
    return {"total": len(matches), "preview": preview,
            "events": [event_data(db, event, host) for event, host in matches[offset:offset + limit]]}


@app.get("/api/events/{event_id}")
def event_detail(event_id: str, db: Session = Depends(get_db)):
    row = db.execute(public_rows(db).where(Event.id == event_id)).first()
    if not row:
        raise HTTPException(404, "Event not found")
    return event_data(db, *row, detailed=True)


@app.get("/api/events/{event_id}/materials/{snapshot_id}/{key}")
def event_poster(event_id: str, snapshot_id: int, key: str, db: Session = Depends(get_db)):
    row = db.execute(public_rows(db).where(Event.id == event_id)).first()
    if row and not row[0].demo:
        for version, item in published_materials(db, event_id, latest_publication(db, event_id), row[1].domains):
            if version == snapshot_id and item.key == key and item.image and (mime := image_mime(item.image)):
                return Response(item.image, media_type=mime,
                                headers={"Content-Security-Policy": "default-src 'none'; sandbox",
                                         "Content-Disposition": "inline"})
    raise HTTPException(404, "Poster not found")


class Preferences(Strict):
    alerts: bool = False
    institutes: list[str] = Field(default_factory=list, max_length=20)


@app.get("/api/account/me")
def profile(student=Depends(auth.require_student), db: Session = Depends(get_db)):
    saved = public_rows(db).join(Saved, Saved.event_id == Event.id).where(Saved.student_id == student.id)
    return {"email": student.email, "preferences": student.preferences,
            "saved": [event_data(db, *row) for row in db.execute(saved)],
            "notifications": [{"id": row.id, "event_id": row.event_id, "message": row.message,
                               "created_at": row.created_at, "read": row.read}
                              for row in db.scalars(select(Notification).where(Notification.student_id == student.id)
                                                   .order_by(Notification.id.desc()).limit(40))]}


@app.put("/api/account/preferences")
def preferences(data: Preferences, student=Depends(auth.require_student), db: Session = Depends(get_db)):
    if set(data.institutes) - set(db.scalars(select(Institute.id))):
        raise HTTPException(400, "Choose institutes from the directory")
    student.preferences = data.model_dump()
    db.commit()
    return student.preferences


@app.put("/api/account/saved/{event_id}")
def save(event_id: str, student=Depends(auth.require_student), db: Session = Depends(get_db)):
    if not db.execute(public_rows(db).where(Event.id == event_id)).first():
        raise HTTPException(404, "Event not found")
    if not db.get(Saved, (student.id, event_id)):
        db.add(Saved(student_id=student.id, event_id=event_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return {"saved": True}


@app.delete("/api/account/saved/{event_id}")
def unsave(event_id: str, student=Depends(auth.require_student), db: Session = Depends(get_db)):
    db.execute(delete(Saved).where(Saved.student_id == student.id, Saved.event_id == event_id))
    db.commit()
    return {"saved": False}


@app.put("/api/account/notifications/{notification_id}")
def read_alert(notification_id: int, student=Depends(auth.require_student), db: Session = Depends(get_db)):
    alert = db.get(Notification, notification_id)
    if not alert or alert.student_id != student.id:
        raise HTTPException(404, "Alert not found")
    alert.read = True
    db.commit()
    return {"read": True}


@app.delete("/api/account")
def delete_account(student=Depends(auth.require_student), db: Session = Depends(get_db)):
    feedback.delete_student_feedback(db, student.id)
    db.delete(student)
    db.commit()
    response = JSONResponse({"deleted": True})
    response.delete_cookie(auth.COOKIE, secure=not auth.local_mode())
    return response


class Submission(Strict):
    institute_id: str = Field(max_length=45)
    source_url: str = Field(max_length=1000)
    notes: str = Field(min_length=12, max_length=2000)
    event_id: str | None = Field(None, max_length=80)


@app.post("/api/submissions", status_code=201)
def submission(data: Submission, student=Depends(auth.require_student), db: Session = Depends(get_db)):
    institute = db.get(Institute, data.institute_id)
    if not institute or not official(data.source_url, institute.domains):
        raise HTTPException(400, "Use an HTTPS announcement on the selected institution's official domain")
    if data.event_id:
        event = db.get(Event, data.event_id)
        if not event or event.institute_id != institute.id or not event.published:
            raise HTTPException(400, "Choose a published event from the selected institution")
    db.add(Review(event_id=data.event_id, kind="submission", proposal=data.model_dump(),
                  context="Account email verified for this submission. Organizer affiliation requires review."))
    db.commit()
    return {"queued": True}


def administrator(x_ajora_admin: str = Header(default="")):
    expected = os.getenv("AJORA_ADMIN_TOKEN", "")
    if len(expected) < 24 or not secrets.compare_digest(expected.encode(), x_ajora_admin.encode()):
        raise HTTPException(401, "Administrator access required")


admin = [Depends(administrator)]


@app.post("/api/automation/refresh", status_code=202)
def daily_refresh(x_ajora_refresh: str = Header(default="")):
    expected = os.getenv("AJORA_REFRESH_TOKEN", "")
    if len(expected) < 24 or not secrets.compare_digest(expected.encode(), x_ajora_refresh.encode()):
        raise HTTPException(401, "Refresh credential required")
    try:
        job_id = enqueue_daily_refresh()
    except (OperationalError, OSError) as error:
        raise HTTPException(503, "Collection queue is unavailable") from error
    return {"status": "queued", "job_id": job_id}


@app.get("/api/admin/reviews", dependencies=admin)
def reviews(db: Session = Depends(get_db)):
    priority = func.coalesce(Review.proposal["priority"].as_integer(), 0)
    rows = db.scalars(select(Review).where(Review.status == "open").order_by(priority.desc(), Review.id.desc()).limit(75))
    return [{"id": row.id, "kind": row.kind, "event_id": row.event_id, "snapshot_id": row.snapshot_id,
             "proposal": row.proposal, "context": row.context, "created_at": row.created_at} for row in rows]


@app.get("/api/admin/snapshots/{snapshot_id}", dependencies=admin)
def evidence_snapshot(snapshot_id: int, db: Session = Depends(get_db)):
    snapshot = db.get(Snapshot, snapshot_id)
    if not snapshot:
        raise HTTPException(404, "Snapshot not found")
    source = db.get(Source, snapshot.source_id)
    institute = db.get(Institute, source.institute_id)
    parsed = parse(snapshot.body, snapshot.kind, snapshot.url, institute.domains)
    return {"url": snapshot.url, "captured_at": snapshot.captured_at, "digest": snapshot.digest,
            "text": parsed.text[:450_000], "application_links": parsed.applications,
            "materials": material_choices(snapshot, institute.domains)}


@app.get("/api/admin/monitor", dependencies=admin)
def monitor(db: Session = Depends(get_db)):
    sources = db.scalars(select(Source).order_by(Source.id))
    runs = db.scalars(select(Run).order_by(Run.id.desc()).limit(25))
    return {"configured_agents": [agent.provider for agent in configured_agents()],
            "sources": [{key: getattr(row, key) for key in (
        "id", "url", "approved", "last_checked", "last_success", "failures", "permission_note")} for row in sources],
        "runs": [{key: getattr(row, key) for key in ("source_id", "outcome", "details", "created_at")} for row in runs]}


class Decision(Strict):
    action: Literal["publish", "reject", "approve_source", "attach_application", "attach_materials", "confirm_schedule_notice"]
    note: str = Field(min_length=8, max_length=1000)
    candidate: Candidate | None = None
    evidence: dict[str, str] = Field(default_factory=dict, max_length=350)
    target_id: str | None = Field(None, max_length=80)
    separate_session: bool = False
    application_confirmed: bool = False
    prices_confirmed: bool = False
    material_keys: list[str] = Field(default_factory=list, max_length=30)
    registration_url: str | None = Field(None, max_length=1000)


@app.post("/api/admin/reviews/{review_id}", dependencies=admin)
def resolve(review_id: int, data: Decision, db: Session = Depends(get_db)):
    claimed = db.execute(update(Review).where(Review.id == review_id, Review.status == "open").values(status="resolving"))
    if claimed.rowcount != 1:
        raise HTTPException(404, "Open review not found")
    review = db.get(Review, review_id)
    try:
        if data.action == "publish":
            if review.kind not in {"candidate", "change", "document"} or not data.candidate:
                raise ValueError("Supply a reviewed candidate and supporting field evidence")
            if not data.target_id and review.proposal.get("possible_matches") and not review.event_id and not data.separate_session:
                raise ValueError("Choose an existing event or confirm that this is a distinct session")
            publish(db, review, data.candidate, data.evidence, data.target_id,
                    application_confirmed=data.application_confirmed, prices_confirmed=data.prices_confirmed,
                    material_keys=data.material_keys)
        elif data.action == "confirm_schedule_notice":
            confirm_schedule_notice(db, review)
        elif data.action == "attach_application":
            if review.kind not in {"document", "candidate", "change"} or not data.target_id or not data.registration_url:
                raise ValueError("Choose a published event and a registration URL from the collected source")
            attach_application(db, review, data.target_id, data.registration_url, data.application_confirmed, data.material_keys)
        elif data.action == "attach_materials":
            if review.kind not in {"document", "candidate", "change"} or not data.target_id:
                raise ValueError("Choose a published event to attach the official flyers or posters")
            attach_materials(db, review, data.target_id, data.material_keys)
        elif data.action == "approve_source":
            if review.kind not in {"source", "submission"}:
                raise ValueError("This review does not propose a crawl source")
            if review.kind == "submission":
                key, url = review.proposal["institute_id"], review.proposal["source_url"]
            else:
                snapshot = db.get(Snapshot, review.snapshot_id)
                key = db.get(Source, snapshot.source_id).institute_id
                url = review.proposal["url"]
            institute = db.get(Institute, key)
            if not official(url, institute.domains):
                raise ValueError("Source must belong to the selected institution")
            source = db.scalar(select(Source).where(Source.url == url))
            if source and source.institute_id != key:
                raise ValueError("Source belongs to another institute")
            if not source:
                source = Source(institute_id=key, url=url)
                db.add(source)
            source.approved, source.permission_note = True, data.note
        review.status = "rejected" if data.action == "reject" else "approved"
        review.resolution, review.resolved_at = data.note, now()
        db.commit()
    except ValueError as error:
        raise HTTPException(400, str(error))
    except IntegrityError:
        raise HTTPException(409, "Another review changed the record. Refresh to continue")
    return {"status": review.status, "event_id": review.event_id}
