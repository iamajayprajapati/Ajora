import hashlib
import os
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pwdlib import PasswordHash
from pydantic import EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import LoginSession, Student, get_db, now
from .schema import Strict

router = APIRouter(prefix="/account")
COOKIE = "ajora_session"
PASSWORDS = PasswordHash.recommended()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


def local_mode():
    return os.getenv("AJORA_LOCAL_DEMO") == "1"


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def future(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")


def optional_student(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE)
    if token and len(token) <= 100:
        login = db.get(LoginSession, hash_token(token))
        if login and login.expires_at > now():
            student = db.get(Student, login.student_id)
            if student and student.verified:
                return student
    return None


def require_student(student=Depends(optional_student)):
    if student is None:
        raise HTTPException(401, "Sign in to continue")
    return student


def session_cookie(db, student, response):
    token = secrets.token_urlsafe(36)
    db.add(LoginSession(token_hash=hash_token(token), student_id=student.id, expires_at=future(24 * 14)))
    db.commit()
    response.set_cookie(COOKIE, token, httponly=True, secure=not local_mode(), samesite="lax", max_age=14 * 86400)


class Credentials(Strict):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class SignUp(Credentials):
    adult: bool
    consent: bool


class Verify(Strict):
    token: str = Field(min_length=20, max_length=100)


def verification_email(address, token):
    message = EmailMessage()
    message["From"] = os.environ["AJORA_SMTP_FROM"]
    message["To"] = address
    message["Subject"] = "Verify your AJORA email"
    link = os.environ["AJORA_PUBLIC_URL"].rstrip("/") + "/my-ajora?" + urlencode({"verify": token})
    message.set_content(f"Open this link to verify your email address. It expires in 24 hours.\n\n{link}")
    with smtplib.SMTP(os.environ["AJORA_SMTP_HOST"], int(os.getenv("AJORA_SMTP_PORT", "587")), timeout=12) as smtp:
        smtp.starttls()
        if os.getenv("AJORA_SMTP_USER"):
            smtp.login(os.environ["AJORA_SMTP_USER"], os.environ["AJORA_SMTP_PASSWORD"])
        smtp.send_message(message)


@router.post("/signup", status_code=201)
def signup(data: SignUp, response: Response, db: Session = Depends(get_db)):
    if not data.adult or not data.consent:
        raise HTTPException(400, "Pilot accounts require age 18+ and agreement to account data use")
    address = data.email.casefold()
    existing = db.scalar(select(Student).where(Student.email == address))
    if existing:
        raise HTTPException(409, "An account already uses this email")
    token = secrets.token_urlsafe(36)
    student = Student(email=address, password=PASSWORDS.hash(data.password), verified=local_mode(),
                      verification_hash=None if local_mode() else hash_token(token),
                      verification_expiry=None if local_mode() else future(24),
                      preferences={"alerts": False, "institutes": []})
    db.add(student)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(409, "An account already uses this email")
    if local_mode():
        session_cookie(db, student, response)
    else:
        try:
            verification_email(address, token)
        except (KeyError, OSError, smtplib.SMTPException):
            raise HTTPException(503, "Email verification is temporarily unavailable")
        db.commit()
    return {"verification_required": not student.verified}


@router.post("/verify")
def verify(data: Verify, db: Session = Depends(get_db)):
    student = db.scalar(select(Student).where(Student.verification_hash == hash_token(data.token)).with_for_update())
    if not student or not student.verification_expiry or student.verification_expiry <= now():
        raise HTTPException(400, "This verification link is invalid or expired")
    student.verified = True
    student.verification_hash = student.verification_expiry = None
    db.commit()
    return {"verified": True}


@router.post("/login")
def login(data: Credentials, response: Response, db: Session = Depends(get_db)):
    student = db.scalar(select(Student).where(Student.email == data.email.casefold()))
    if not PASSWORDS.verify(data.password, student.password if student else DUMMY_HASH) or not student:
        raise HTTPException(401, "Email or password is incorrect")
    if not student.verified:
        raise HTTPException(403, "Verify your email before signing in")
    db.execute(delete(LoginSession).where(LoginSession.student_id == student.id, LoginSession.expires_at <= now()))
    session_cookie(db, student, response)
    return {"email": student.email}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    if token := request.cookies.get(COOKIE):
        db.execute(delete(LoginSession).where(LoginSession.token_hash == hash_token(token)))
        db.commit()
    response.delete_cookie(COOKIE, secure=not local_mode())
    return {"signed_out": True}
