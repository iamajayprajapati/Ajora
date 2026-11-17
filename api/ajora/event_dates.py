import re
from datetime import date

MONTHS = dict(zip(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), range(1, 13)))
MONTH = r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
DAY = r"\d{1,2}(?:st|nd|rd|th)?"
RANGE_SEPARATOR = r"(?:\s*[-–—]\s*|\s+to\s+)"
DEADLINE = re.compile(
    r"\b(?:(?:registration|enrolment|enrollment|applications?)\b[^.;]{0,40}\b(?:deadline|closes?|closing|ends?|until)|"
    r"(?:last date|deadline)\s+(?:for|to)\s+(?:online\s+)?(?:register|registration|apply|applications?)|register by|apply by)\b",
    re.I,
)
FORMATS = (
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), lambda m: (m[1], m[2], m[3])),
    (re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)"), lambda m: (m[3], m[2], m[1])),
    (re.compile(rf"\b({DAY})[\s.-]+({MONTH})[\s,.-]+(\d{{4}})\b", re.I),
     lambda m: (m[3], MONTHS[m[2][:3].lower()], m[1])),
    (re.compile(rf"\b({MONTH})\.?\s+({DAY})(?:{RANGE_SEPARATOR}({DAY}))?,?\s+(\d{{4}})\b", re.I),
     lambda m: (m[4], MONTHS[m[1][:3].lower()], m[2])),
)
RANGE = re.compile(rf"\b({DAY}){RANGE_SEPARATOR}({DAY})[\s.-]+({MONTH})[\s,.-]+(\d{{4}})\b", re.I)
CROSS_MONTH = re.compile(
    rf"\b({MONTH})\.?\s+({DAY}){RANGE_SEPARATOR}({MONTH})\.?\s+({DAY}),?\s+(\d{{4}})\b", re.I,
)


def dates_in(text):
    dates = set()
    def add(year, month, day):
        try:
            dates.add(date(int(year), int(month), int(re.match(r"\d+", day)[0])))
        except ValueError:
            pass
    for pattern, parts in FORMATS:
        for match in pattern.finditer(text):
            add(*parts(match))
            if pattern is FORMATS[-1][0] and match[3]:
                add(match[4], MONTHS[match[1][:3].lower()], match[3])
    for match in RANGE.finditer(text):
        add(match[4], MONTHS[match[3][:3].lower()], match[1])
    for match in CROSS_MONTH.finditer(text):
        first, last = MONTHS[match[1][:3].lower()], MONTHS[match[3][:3].lower()]
        if first <= last:
            add(match[5], first, match[2])
    return dates


def verify_dates(candidate, evidence, structured):
    for field in ("starts", "ends", "registration_deadline"):
        value = getattr(candidate, field)
        if value is None:
            continue
        quote = evidence.get(field, "")
        matched = dates_in(quote)
        if field == "registration_deadline":
            if matched != {value} or not DEADLINE.search(quote):
                raise ValueError("Registration deadline needs an explicit deadline label and an exact date including the year")
        else:
            if any(source.title == candidate.title and getattr(source, field) == value
                   and claims.get(field) == quote for source, claims in structured):
                continue
            boundary = (min(matched) if field == "starts" else max(matched)) if matched else None
            if value != boundary or DEADLINE.search(quote):
                raise ValueError(f"{field} needs an exact event date including the year")
