import io
import re
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup
from PIL import Image
from sqlalchemy import select

from .collect import digest, official
from .db import Review, Snapshot, Source
from .registration import MAX_POSTERS, poster_assets, unpack_html

MATERIAL_NAME = re.compile(r"\b(?:e[ -]?)?(?:flyers?|fliers?|posters?|brochures?|leaflets?)\b", re.I)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
IMAGE_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}


@dataclass(frozen=True)
class Material:
    key: str
    label: str
    kind: str
    url: str | None
    image: bytes | None = None


def image_mime(body):
    try:
        with Image.open(io.BytesIO(body), formats=tuple(IMAGE_FORMATS)) as image:
            if image.width * image.height > 12_000_000:
                return None
            mime = IMAGE_FORMATS[image.format]
            image.verify()
            return mime
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def snapshot_materials(snapshot, domains):
    url, body, kind = snapshot.url, snapshot.body, snapshot.kind
    if not official(url, domains):
        return []
    if kind == "pdf":
        return [Material("original", "Official flyer or brochure", "pdf", url)]
    if kind == "image":
        return [Material("original", "Official poster", "image", url, body)] if image_mime(body) else []
    if kind not in {"html", "html_bundle"}:
        return []

    items = {}
    if kind == "html_bundle":
        body, assets = unpack_html(body)
        for index, (location, image) in enumerate(assets):
            if official(location, domains) and image_mime(image):
                items[location] = Material(f"poster-{index}", "Poster from the announcement", "image", location, image)
    page = BeautifulSoup(body, "html.parser")
    inline = 0
    for location, image in poster_assets(page, url, named_inline=True):
        if image is None:
            if official(location, domains) and location not in items and len(items) < 30:
                items[location] = Material(f"remote-{len(items)}", "Poster from the announcement", "image", location)
            continue
        if inline >= MAX_POSTERS:
            break
        inline += 1
        if image_mime(image):
            key = f"inline-{inline}"
            items[key] = Material(key, location, "image", None, image)

    for index, link in enumerate(page.find_all("a", href=True, limit=1000)):
        try:
            target = urldefrag(urljoin(url, link["href"].strip())).url
        except ValueError:
            continue
        if not official(target, domains) or target in items:
            continue
        path = urlsplit(target).path.lower()
        label = link.get_text(" ", strip=True) or str(link.get("aria-label") or link.get("title") or "")
        if not MATERIAL_NAME.search(f"{label} {path.replace('_', ' ')}"):
            continue
        if path.endswith(".pdf") or path.endswith(IMAGE_EXTENSIONS):
            media = "pdf" if path.endswith(".pdf") else "image"
            description = label[:100] if MATERIAL_NAME.search(label) else "Event brochure" if media == "pdf" else "Event poster"
            items[target] = Material(f"linked-{index}", description, media, target)
        if len(items) >= 30:
            break
    return list(items.values())


def material_choices(snapshot, domains):
    return [{"key": item.key, "label": item.label, "kind": item.kind, "url": item.url,
             "fingerprint": digest(item.image) if item.image else None}
            for item in snapshot_materials(snapshot, domains)]


def select_materials(snapshot, domains, keys):
    options = {item.key for item in snapshot_materials(snapshot, domains)}
    if set(keys) - options:
        raise ValueError("Choose flyers and posters captured in this official announcement")
    return sorted(set(keys) | (options & {"original"}))


def published_materials(db, event_id, publication, domains):
    attached = db.scalars(select(Review).where(Review.event_id == event_id, Review.status == "approved",
                                              Review.kind.in_(("application", "material")))
                          .order_by(Review.resolved_at.desc(), Review.id.desc()))
    seen_sources, seen_materials = set(), set()
    reviews = sorted(filter(None, (publication, *attached)), key=lambda review: (review.resolved_at or "", review.id), reverse=True)
    for review in reviews:
        snapshot = db.get(Snapshot, review.snapshot_id) if review and review.snapshot_id else None
        if not snapshot or snapshot.source_id in seen_sources:
            continue
        seen_sources.add(snapshot.source_id)
        source = db.get(Source, snapshot.source_id)
        if not source or not source.approved:
            continue
        selected = set(review.proposal.get("public_materials", ("original",)))
        for item in snapshot_materials(snapshot, domains):
            identity = item.url or digest(item.image)
            if item.key in selected and identity not in seen_materials:
                seen_materials.add(identity)
                yield snapshot.id, item
