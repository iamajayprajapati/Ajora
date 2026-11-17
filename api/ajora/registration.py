import base64
import binascii
import io
import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import pymupdf
import zxingcpp
from PIL import Image

from .schema import application_url

REGISTER = re.compile(r"\b(?:register(?:\s+(?:now|here))?|registration|apply(?:\s+(?:now|here))?|application form|sign[ -]?up|enrol(?:l|ment)?|book (?:a|your) (?:seat|place))\b", re.I)
POSTER = re.compile(r"\b(?:qr[ _-]?code|qrcode|qr|poster|flyer|brochure)\b", re.I)
PRINTED_URL = re.compile(r'https://[^\s<>"\x27]+')
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_POSTER_BYTES = 1_500_000
MAX_POSTERS = 3


@dataclass(frozen=True)
class ApplicationLink:
    url: str
    method: str
    location: str
    label: str = ""


def make_link(value, method, location, label=""):
    if isinstance(value, str) and application_url(value):
        return ApplicationLink(value, method, location, label[:180])
    return None


def qr_links(image, location):
    codes = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)
    for code in codes:
        try:
            value = code.bytes.decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if link := make_link(value, "QR code", location):
            yield link


def image_links(body, location):
    try:
        with Image.open(io.BytesIO(body), formats=("PNG", "JPEG", "WEBP", "GIF")) as image:
            if image.width * image.height <= 12_000_000:
                return list(qr_links(image.convert("L"), location))
    except (OSError, ValueError, RuntimeError, Image.DecompressionBombError):
        pass
    return []


def pdf_links(page):
    location = f"PDF page {page.number + 1}"
    results = []
    for item in page.get_links():
        if item.get("kind") == pymupdf.LINK_URI:
            rect = item["from"]
            label = page.get_textbox(rect).strip()
            if link := make_link(item.get("uri"), "PDF hyperlink", location, label):
                results.append(link)
    text = page.get_text()
    for line in text.splitlines():
        if REGISTER.search(line):
            for value in PRINTED_URL.findall(line):
                if link := make_link(value.rstrip(".,);]"), "PDF text", location, line):
                    results.append(link)
    # Bound rendering separately from text extraction on long brochures.
    if page.number < 20 and page.rect.width > 0 and page.rect.height > 0:
        scale = min(3, 2200 / max(page.rect.width, page.rect.height))
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False)
        image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
        results.extend(qr_links(image, location))
    return text, results


def poster_assets(doc, page_url, *, named_inline=False):
    for image in doc.find_all("img", limit=400):
        caption = " ".join(str(image.get(key) or "") for key in ("alt", "title", "class", "id"))
        sources = {str(image.get(key) or "").strip() for key in ("data-src", "src")}
        for value in sorted(sources):
            if not value:
                continue
            if value.startswith("data:"):
                if named_inline and not POSTER.search(caption):
                    continue
                mime, separator, encoded = value.partition(";base64,")
                if separator and mime[5:] in IMAGE_TYPES and len(encoded) <= MAX_POSTER_BYTES * 4 // 3 + 4:
                    try:
                        yield "Embedded poster image", base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError):
                        continue
            else:
                try:
                    if POSTER.search(f"{caption} {urlsplit(value).path.replace('_', ' ')}"):
                        yield urljoin(page_url, value), None
                except ValueError:
                    continue


def bundle_html(body, assets):
    return json.dumps({"html": base64.b64encode(body).decode("ascii"),
                       "assets": [(location, base64.b64encode(image).decode("ascii")) for location, image in assets]},
                      separators=(",", ":")).encode()


def unpack_html(body):
    bundle = json.loads(body)
    return base64.b64decode(bundle["html"]), [
        (location, base64.b64decode(image)) for location, image in bundle["assets"]
    ]


def distinct_links(items):
    return list(dict.fromkeys(items))[:60]
