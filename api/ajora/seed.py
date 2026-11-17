from datetime import timedelta
from urllib.parse import urlsplit

from .db import Event, Institute, SiteCounter, Source, now
from .registry import DIRECTORY, current_website
from .schema import Candidate
from .workflow import today

PILOT_SOURCES = {
    "iit-bombay": "https://www.iitb.ac.in/events",
    "iit-delhi": "https://home.iitd.ac.in/",
    "iit-madras": "https://www.iitm.ac.in/",
    "iit-kanpur": "https://www.iitk.ac.in/",
    "iit-kharagpur": "https://www.iitkgp.ac.in/",
    "nit-trichy": "https://www.nitt.edu/home/",
    "nit-surathkal": "https://www.nitk.ac.in/",
    "nit-warangal": "https://www.nitw.ac.in/",
    "nit-rourkela": "https://www.nitrkl.ac.in/",
    "nit-calicut": "https://nitc.ac.in/",
    "iiit-allahabad": "https://www.iiita.ac.in/",
    "iiit-gwalior": "https://www.iiitm.ac.in/",
    "iiit-jabalpur": "https://www.iiitdmj.ac.in/",
    "iiit-kancheepuram": "https://www.iiitdm.ac.in/",
    "iiit-guwahati": "https://www.iiitg.ac.in/",
    "delhi-university": "https://www.du.ac.in/",
    "jnu": "https://www.jnu.ac.in/",
    "bhu": "https://www.bhu.ac.in/",
    "uoh": "https://uohyd.ac.in/",
    "tezu": "https://www.tezu.ac.in/",
}


def seed(db, demo=False):
    from sqlalchemy import select

    if not db.get(SiteCounter, "visitors"):
        db.add(SiteCounter(name="visitors", total=0))
    for entry in DIRECTORY.values():
        institute = db.get(Institute, entry.id)
        if institute is None:
            institute = Institute(id=entry.id, domains=[])
            db.add(institute)
        institute.name = entry.name
        institute.category = entry.category
        institute.city, institute.state = entry.city, entry.state
        institute.registry_url = entry.reference
        canonical_domain = urlsplit(current_website(entry)).hostname.removeprefix("www.")
        institute.domains = list(dict.fromkeys([canonical_domain, entry.domain, *institute.domains]))
    db.flush()
    for key, url in PILOT_SOURCES.items():
        if not db.scalar(select(Source.id).where(Source.url == url)):
            db.add(Source(institute_id=key, url=url))
    if not demo:
        return
    examples = [
        ("iit-bombay", "Applied AI and Machine Learning", "Workshop", "AI & Technology", "online", 0, 0, ["UG", "PG"], 8,
         "Build and evaluate a simple machine-learning model using a small, real-world dataset."),
        ("nit-trichy", "Build for Bharat Hackathon", "Hackathon", "Engineering", "hybrid", 0, None, ["Class 9–12", "UG"], 13,
         "Work in a team to prototype a solution for a practical challenge in your community."),
        ("iiit-allahabad", "Cybersecurity Essentials Bootcamp", "Training", "Cybersecurity", "online", 299, 0, ["UG", "PG"], 19,
         "Practice identifying common web vulnerabilities in a guided lab environment."),
        ("uoh", "Research Methods for Beginners", "Seminar", "Research", "offline", 0, 0, ["PG", "PhD"], 22,
         "Develop a research question, choose a method, and sketch an analysis plan."),
        ("iit-madras", "Designing for a Greener Future", "Workshop", "Sustainability", "online", 0, 0, ["Class 9–12", "UG"], 25,
         "Explore how to measure the environmental impact of everyday design choices."),
        ("jnu", "Indian Languages and Digital Archives", "Conference", "Humanities", "offline", 500, None, ["UG", "PG", "PhD"], 29,
         "Meet researchers working on accessible digital archives for Indian languages."),
    ]
    for index, (institute, title, category, subject, mode, fee, certificate, levels, offset, description) in enumerate(examples):
        key = f"sample-{index + 1}"
        if db.get(Event, key):
            continue
        starts = today() + timedelta(days=offset)
        candidate = Candidate(title=title, category=category, description=description, starts=starts,
                             registration_deadline=starts - timedelta(days=3), subject=[subject],
                             attendance=[{"mode": mode, "levels": levels, "fee_inr": fee,
                                          "certificate": "provided" if certificate is not None else "unknown",
                                          "certificate_fee_inr": certificate}])
        db.add(Event(id=key, institute_id=institute, status="published", starts=starts.isoformat(),
                     published=candidate.model_dump(mode="json"), demo=True, checked_at=now()))
