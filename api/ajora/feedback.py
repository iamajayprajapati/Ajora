from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from .auth import optional_student, require_student
from .db import FeedbackPost, get_db
from .schema import Strict

router = APIRouter(prefix="/feedback")
PostID = Annotated[int, Path(gt=0, le=2_147_483_647)]


class Message(Strict):
    body: str = Field(min_length=3, max_length=2000)


class Topic(Message):
    title: str = Field(min_length=5, max_length=120)
    category: Literal["Suggestion", "Issue", "General"]


def public_post(post, student, preview=False):
    return {"id": post.id, "title": post.title, "category": post.category,
            "body": post.body[:300] if preview else post.body, "removed": post.removed,
            "created_at": post.created_at, "can_remove": bool(student and student.id == post.student_id)}


def forum_query():
    reply = aliased(FeedbackPost)
    count = (select(func.count(reply.id)).where(reply.parent_id == FeedbackPost.id)
             .correlate(FeedbackPost).scalar_subquery())
    return (select(FeedbackPost, count.label("reply_count"))
            .where(FeedbackPost.parent_id.is_(None), or_(FeedbackPost.removed.is_(False), count > 0)))


def count_page(rows, size):
    return rows[:size], len(rows) > size


def insert_post(db, student, **values):
    recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    sent = db.scalar(select(func.count()).select_from(FeedbackPost).where(
        FeedbackPost.student_id == student.id, FeedbackPost.created_at >= recent))
    if sent >= 8:
        raise HTTPException(429, "Wait a minute before posting again")
    post = FeedbackPost(student_id=student.id, **values)
    db.add(post)
    db.commit()
    return post


@router.get("")
def list_topics(offset: int = Query(0, ge=0, le=1_000_000), student=Depends(optional_student), db: Session = Depends(get_db)):
    size = 20
    query = forum_query().order_by(FeedbackPost.id.desc()).offset(offset).limit(size + 1)
    page, more = count_page(db.execute(query).all(), size)
    return {"topics": [public_post(post, student, preview=True) | {"reply_count": replies} for post, replies in page],
            "has_more": more}


@router.post("", status_code=201)
def create_topic(data: Topic, student=Depends(require_student), db: Session = Depends(get_db)):
    return public_post(insert_post(db, student, **data.model_dump()), student)


@router.get("/{topic_id}")
def get_topic(topic_id: PostID, offset: int = Query(0, ge=0, le=1_000_000),
              student=Depends(optional_student), db: Session = Depends(get_db)):
    row = db.execute(forum_query().where(FeedbackPost.id == topic_id)).first()
    if not row:
        raise HTTPException(404, "Discussion not found")
    topic, count = row
    size = 25
    replies, more = count_page(list(db.scalars(select(FeedbackPost).where(FeedbackPost.parent_id == topic_id)
                                               .order_by(FeedbackPost.id.desc()).offset(offset).limit(size + 1))), size)
    return {"topic": public_post(topic, student) | {"reply_count": count},
            "replies": [public_post(post, student) for post in replies], "has_more": more}


@router.post("/{topic_id}/replies", status_code=201)
def create_reply(topic_id: PostID, data: Message, student=Depends(require_student), db: Session = Depends(get_db)):
    topic = db.get(FeedbackPost, topic_id, with_for_update=True)
    if not topic or topic.parent_id is not None or topic.removed:
        raise HTTPException(404, "This discussion is no longer open")
    return public_post(insert_post(db, student, parent_id=topic_id, body=data.body), student)


def clear_topic():
    return {"body": "", "title": None, "student_id": None, "removed": True}


@router.delete("/posts/{post_id}")
def remove_post(post_id: PostID, student=Depends(require_student), db: Session = Depends(get_db)):
    post = db.get(FeedbackPost, post_id, with_for_update=True)
    if not post or post.student_id != student.id:
        raise HTTPException(404, "Post not found")
    if post.parent_id is None:
        for field, value in clear_topic().items():
            setattr(post, field, value)
    else:
        db.delete(post)
    db.commit()
    return {"removed": True}


def delete_student_feedback(db, student_id):
    authored = FeedbackPost.student_id == student_id
    db.execute(update(FeedbackPost).where(authored, FeedbackPost.parent_id.is_(None)).values(**clear_topic()))
    db.execute(delete(FeedbackPost).where(authored, FeedbackPost.parent_id.is_not(None)))
