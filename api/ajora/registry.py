import csv
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

JOSAA = "https://josaa.admissions.nic.in/applicant/seatmatrix/instituteview.aspx"
UGC = "https://www.ugc.gov.in/universitydetails/university?type=ddmCMsxJZgXH2S%2Fm0uMOKQ%3D%3D"


@dataclass(frozen=True)
class DirectoryEntry:
    id: str
    name: str
    category: str
    city: str
    state: str
    website: str
    subgroup: str

    @property
    def domain(self):
        return urlsplit(self.website).hostname.removeprefix("www.")

    @property
    def reference(self):
        if self.category == "Central University":
            return UGC
        if self.subgroup == "Other IIITs":
            return self.website
        return JOSAA


with Path(__file__).with_name("institutes.csv").open(encoding="utf-8", newline="") as file:
    DIRECTORY = {row["id"]: DirectoryEntry(**row) for row in csv.DictReader(file)}

identity_path = Path(__file__).with_name("institute_identity.json")
IDENTITY = json.loads(identity_path.read_text(encoding="utf-8")) if identity_path.is_file() else {}
website_path = Path(__file__).with_name("institute_websites.json")
WEBSITE_CHECKS = json.loads(website_path.read_text(encoding="utf-8")) if website_path.is_file() else {}


def current_website(entry):
    check = WEBSITE_CHECKS.get(entry.id, {})
    return check.get("website", entry.website)


def introduction(entry):
    location = ", ".join(dict.fromkeys(filter(None, (entry.city, entry.state))))
    where = f" in {location}" if location else ""
    if entry.category == "IIT":
        return f"{entry.name} is an Indian Institute of Technology{where}. It focuses on higher education and research in engineering, science and technology."
    if entry.category == "NIT":
        return f"{entry.name} is a National Institute of Technology{where}. It offers higher education and research in engineering and related fields."
    if entry.category == "IIIT":
        if entry.subgroup == "Additional campus":
            return f"{entry.name} is the Diu campus of IIIT Vadodara. It focuses on education in information technology and related fields."
        funding = {"Centrally funded": "centrally funded institute", "PPP institutes": "public–private partnership institute"}.get(
            entry.subgroup, "institute")
        return f"{entry.name} is a {funding}{where} focused on computing, information technology and related fields."
    focus_areas = {
        "agricultural": "agriculture and allied disciplines", "sanskrit": "Sanskrit language and scholarship",
        "aviation": "aviation education", "maritime": "maritime education and training",
        "sports": "sports education", "open university": "open and distance education",
        "tribal": "expanding access to higher education, including for tribal communities",
        "foreign languages": "language education", "national urdu": "Urdu-medium education",
    }
    focus = next((value for keyword, value in focus_areas.items() if keyword in entry.name.casefold()), None)
    start = f"{entry.name} is a UGC-listed central university based{where}." if where else f"{entry.name} is listed in the UGC’s Central Universities directory."
    return f"{start} It focuses on {focus}." if focus else start
