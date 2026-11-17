"""Recheck AJORA institute homepages without downloading full sites."""
import argparse
import json
import re
import ssl
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .registry import DIRECTORY

AUDIT = Path(__file__).with_name("institute_websites.json")
# Independently confirmed institute-operated alternate homepages.
ALTERNATES = {
    "iit-kharagpur": "https://www.iitkgp.ac.in/home",
    "tezu": "https://www.tezu.ernet.in/",
    "central-university-of-punjab": "https://www.cup.edu.in/",
    "jamia-millia-islamia": "https://www.jmi.ac.in/",
    "mahatma-gandhi-antarrashtriya-hindi-vishwavidyalaya": "https://new.hindivishwa.org/",
    "nit-andhra-pradesh": "https://nitandhra.ac.in/main/",
    "nit-manipur": "https://new.nitmanipur.ac.in/",
    "aligarh-muslim-university": "https://www.amu.ac.in/?v=1.0.24",
    "sindhu-central-university": "https://www.sindhucu.ac.in/",
    "iiit-naya-raipur": "https://www.iiitnr.ac.in/",
}

# Official homepage HTML was confirmed separately for these sites.
INDEPENDENT_CHECKS = {
    "iit-hyderabad": ("https://iith.ac.in/", "IIT Hyderabad"),
    "iit-gandhinagar": ("https://iitgn.ac.in/", "IIT Gandhinagar"),
    "nit-puducherry": ("https://nitpy.ac.in/", "NIT Puducherry"),
    "nit-manipur": ("https://new.nitmanipur.ac.in/", "NIT Manipur"),
    "nit-andhra-pradesh": ("https://nitandhra.ac.in/main/", "NIT Andhra Pradesh"),
    "aligarh-muslim-university": ("https://www.amu.ac.in/?v=1.0.24", "Aligarh Muslim University"),
    "sindhu-central-university": ("https://www.sindhucu.ac.in/", "Sindhu Central University"),
}
INDEPENDENT_CHECK_DATE = "2026-11-17"


def verify(entry, logo_source):
    original = entry.website
    alt = ALTERNATES.get(entry.id)
    hosts = {entry.domain}
    if alt:
        hosts.add(urlsplit(alt).hostname.removeprefix("www."))
    variants = [original, alt]
    parsed = urlsplit(original)
    alternate_host = parsed.hostname.removeprefix("www.") if parsed.hostname.startswith("www.") else "www." + parsed.hostname
    variants.append(parsed._replace(netloc=alternate_host).geturl())
    evidence = {"registry_url": entry.reference, "checked_on": datetime.now(timezone.utc).isoformat(),
                "status": "unavailable", "website": original, "logo_source": logo_source}
    with httpx.Client(verify=ssl.create_default_context(), timeout=httpx.Timeout(5),
                      headers={"User-Agent": "AJORA Directory Check/1.0", "Accept": "text/html"}) as client:
        for url in dict.fromkeys(filter(None, variants)):
            start = url
            try:
                for _ in range(5):
                    host = urlsplit(url).hostname
                    if urlsplit(url).scheme != "https" or not host or not any(host == root or host.endswith("." + root) for root in hosts):
                        raise ValueError("Redirect needs independent review")
                    with client.stream("GET", url) as response:
                        if response.is_redirect:
                            url = urljoin(url, response.headers["location"])
                            continue
                        if response.status_code != 200 or "html" not in response.headers.get("content-type", "").lower():
                            break
                        snippet = next(response.iter_bytes(), b"")
                        header = BeautifulSoup(snippet[:70_000], "html.parser")
                        title = " ".join(header.title.get_text(" ", strip=True).split()) if header.title else ""
                        if re.search(r"forbidden|access denied|just a moment|page not found|blocked|domain for sale", title, re.I):
                            break
                        if not snippet or not re.search(rb"<!doctype|<html|<head", snippet[:2000], re.I):
                            break
                        evidence.update(status="reachable", website=url, title=title[:150], checked_url=start)
                        return evidence
            except (ValueError, httpx.HTTPError, KeyError):
                continue
    independent = INDEPENDENT_CHECKS.get(entry.id)
    if independent:
        evidence.update(status="official_confirmed", website=independent[0], title=independent[1],
                        homepage_confirmed_on=INDEPENDENT_CHECK_DATE)
    elif alt:
        evidence["website"] = alt
    return evidence


def main():
    from .registry import IDENTITY

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=AUDIT)
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=16) as pool:
        jobs = {entry.id: pool.submit(verify, entry, IDENTITY.get(entry.id, {}).get("logo_source"))
                for entry in DIRECTORY.values()}
        results = {key: job.result() for key, job in jobs.items()}
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Reachable official homepages: {sum(row['status']=='reachable' for row in results.values())}/{len(results)}")


if __name__ == "__main__":
    main()
