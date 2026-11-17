import re
from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Mode = Literal["online", "offline", "hybrid"]
Level = Literal["Class 9–12", "UG", "PG", "PhD", "Faculty"]
Category = Literal["Workshop", "Conference", "Hackathon", "Seminar", "Training", "Summer school", "FDP", "Other"]
CATEGORIES = ("IIT", "NIT", "IIIT", "Central University")


def https_url(value: str) -> bool:
    if re.search(r"[\s\\\x00-\x1f]", value):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == "https" and bool(url.hostname) and url.netloc.isascii()
                and url.username is None and url.password is None and url.port in (None, 443))
    except ValueError:
        return False


def application_url(value: str) -> bool:
    if len(value) > 1000 or not https_url(value):
        return False
    host = urlsplit(value).hostname or ""
    return bool(re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})", host)
                and host.rsplit(".", 1)[-1] not in {"local", "localhost", "internal", "test", "invalid"})


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Attendance(Strict):
    mode: Mode
    levels: list[Level] = Field(default_factory=list, max_length=5)
    fee_inr: int | None = Field(None, ge=0, le=10_000_000, strict=True)
    certificate_fee_inr: int | None = Field(None, ge=0, le=10_000_000, strict=True)
    certificate: Literal["provided", "unavailable", "unknown"] = "unknown"
    certificate_conditions: str | None = Field(None, max_length=500)
    conditions: str | None = Field(None, max_length=500)
    deposit_inr: int | None = Field(None, ge=0, strict=True)

    @model_validator(mode="after")
    def check_certificate(self):
        if self.certificate != "provided" and (self.certificate_fee_inr is not None or self.certificate_conditions):
            raise ValueError("Certificate terms require a confirmed certificate")
        return self

class Entity(Strict):
    name: str = Field(min_length=2, max_length=150)
    role: Literal["organizer", "department", "speaker", "sponsor", "coordinator", "partner", "certificate issuer", "funding agency"]
    details: str | None = Field(None, max_length=600)


class Candidate(Strict):
    title: str = Field(min_length=5, max_length=220)
    category: Category = "Other"
    description: str | None = Field(None, max_length=2400)
    starts: date
    ends: date | None = None
    registration_deadline: date | None = None
    announced: date | None = None
    registration_url: str | None = Field(None, max_length=1000)
    status: Literal["scheduled", "cancelled", "postponed"] = "scheduled"
    venue: str | None = Field(None, max_length=200)
    subject: list[str] = Field(default_factory=list, max_length=8)
    attendance: list[Attendance] = Field(default_factory=list, max_length=12)
    entities: list[Entity] = Field(default_factory=list, max_length=30)
    eligibility_notes: str | None = Field(None, max_length=800)
    assistance: str | None = Field(None, max_length=800)
    language: str | None = Field(None, max_length=80)
    difficulty: str | None = Field(None, max_length=80)
    learning_outcomes: str | None = Field(None, max_length=800)

    @field_validator("registration_url")
    @classmethod
    def check_url(cls, value):
        if value is not None and not application_url(value):
            raise ValueError("A public HTTPS registration URL is required")
        return value

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.ends and self.ends < self.starts:
            raise ValueError("End date precedes start date")
        if self.registration_deadline and self.registration_deadline > (self.ends or self.starts):
            raise ValueError("Registration deadline follows the event")
        return self


def claim_fields(candidate: Candidate) -> set[str]:
    values = candidate.model_dump(exclude_defaults=True, exclude_none=True, mode="json")
    fields = {key for key in values.keys() - {"attendance", "entities"} if values[key] not in ("", [])}
    for collection in ("attendance", "entities"):
        for index, item in enumerate(getattr(candidate, collection)):
            fields |= {f"{collection}.{index}.{key}" for key, value in
                       item.model_dump(exclude_defaults=True, exclude_none=True).items() if value not in ("", [])}
    return fields
