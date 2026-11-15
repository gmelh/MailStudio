"""Checks that a campaign's images will show, look sharp and make sense when blocked.

Run on the rendered email, so every image is covered whatever section it's in (the
brand logo fallback and stretched headers included), with its real address, the width
it's displayed at and its description.

Problems stop a campaign from being sent, because nobody would see the image:
- an address only this computer or network can reach (localhost, 192.168.x.x, …);
- a MailStudio image that isn't on this install (deleted, or uploaded to another one).
On an install that is itself on a local address (development, or trying MailStudio out),
local images are only a warning, so test emails to Mailpit and the like still work.

Warnings are shown before sending, because the email still works without fixing them:
- no description (alt text): shown when images are blocked, and read out by screen readers;
- a placeholder description like "Company logo", or a file name;
- plain http: some mail apps won't load it;
- an image narrower than it's displayed, which looks blurry;
- images that are heavy for mobile data;
- an email that's mostly images and little text, which spam filters dislike.

`absolute_assets` also points MailStudio's own images at the install's current address,
so they keep working after its address changes (http to https, a new domain).
"""

from __future__ import annotations

import html as html_lib
import ipaddress
import re
import sqlite3
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from . import assets

_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r'([a-zA-Z-]+)="([^"]*)"')
_SECTION = re.compile(r'class="ms-s-([A-Za-z0-9_-]+)')
_BACKGROUND = re.compile(r"""background="(https?://[^"]+)"|url\(['"]?(https?://[^'")]+)['"]?\)""")
_OWN_ASSET = re.compile(r'https?://[^/"\'\s>]+(/assets/(ast_[0-9A-Z]{26})\.(?:jpg|png|gif))')

PLACEHOLDER_ALTS = {
    "",
    "image",
    "img",
    "photo",
    "picture",
    "pic",
    "logo",
    "banner",
    "graphic",
    "icon",
    "company logo",
    "featured image",
    "story image",
    "header image",
    "untitled",
    "placeholder",
}
_FILE_NAME = re.compile(
    r"^[\w\- ]+\.(jpe?g|png|gif|webp|bmp)$|^(img|dsc|image|screenshot)[\s_-]?\d+", re.IGNORECASE
)
_LOCAL_SUFFIXES = (".local", ".lan", ".internal", ".home", ".home.arpa", ".localdomain", ".test", ".invalid")

HEAVY_IMAGE_BYTES = 500 * 1024
HEAVY_EMAIL_BYTES = 1024 * 1024
MIN_WORDS_WITH_IMAGES = 60
"""Fewer words than this (footer included) next to images reads as an image-only email."""


@dataclass(frozen=True, slots=True)
class Found:
    src: str
    alt: str | None
    width: int | None
    section_id: str | None
    background: bool = False
    """A background image (the Hero image section): it has no description of its own."""


@dataclass(frozen=True, slots=True)
class Report:
    problems: list[str]
    warnings: list[str]

    def as_dict(self) -> dict[str, list[str]]:
        return {"problems": self.problems, "warnings": self.warnings}


def absolute_assets(html: str, base_url: str) -> str:
    """Point MailStudio's own images at the install's current address."""
    base = base_url.rstrip("/")
    return _OWN_ASSET.sub(lambda m: base + m.group(1), html)


def find(html: str) -> list[Found]:
    """The images in a rendered email, with the section each one is in."""
    found = []
    for match in _IMG.finditer(html):
        attrs = {k.lower(): html_lib.unescape(v) for k, v in _ATTR.findall(match.group(0))}
        if not attrs.get("src"):
            continue
        sections = _SECTION.findall(html, 0, match.start())
        width = attrs.get("width", "")
        found.append(
            Found(
                src=attrs["src"],
                alt=attrs.get("alt"),
                width=int(width) if width.isdigit() else None,
                section_id=sections[-1] if sections else None,
            )
        )
    seen = set()
    for match in _BACKGROUND.finditer(html):
        src = html_lib.unescape(match.group(1) or match.group(2))
        sections = _SECTION.findall(html, 0, match.start())
        section_id = sections[-1] if sections else None
        if (src, section_id) in seen:  # the same image appears again for Outlook
            continue
        seen.add((src, section_id))
        found.append(Found(src=src, alt=None, width=None, section_id=section_id, background=True))
    return found


def unreachable(url: str) -> str | None:
    """Why recipients can't load this address, or None if they can."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return "isn't a web address"
    if host == "localhost" or host.endswith(".localhost") or host.endswith(_LOCAL_SUFFIXES):
        return f"points at {host}, which only this computer or network can reach"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            return f"points at {host}, a name only your own network knows"
        return None
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
        return f"points at {host}, a private address recipients can't reach"
    return None


def check(
    conn: sqlite3.Connection,
    html: str,
    text: str,
    sections: list[tuple[str, str]],
    base_url: str,
) -> Report:
    """`sections` is [(section id, its label)] in order, to say where each image is.
    `base_url` is the install's own address."""
    local_install = unreachable(base_url) is not None
    where = {section_id: f"section {n} ({label})" for n, (section_id, label) in enumerate(sections, start=1)}
    problems: list[str] = []
    warnings: list[str] = []
    seen_assets: dict[str, dict[str, Any]] = {}

    images = find(html)
    for image in images:
        place = where.get(image.section_id or "", "An image")
        name = f"The image in {place}" if image.section_id in where else place

        reason = unreachable(image.src)
        if reason:
            if local_install:
                warnings.append(
                    f"{name} {reason}, so only you will see it. Before sending to real subscribers, "
                    "upload it on your MailStudio's public address."
                )
            else:
                problems.append(f"{name} {reason}. Upload it here, on your MailStudio's public address.")
            continue
        own = _OWN_ASSET.match(image.src)
        asset = assets.get(conn, own.group(2)) if own else None
        if own and asset is None:
            problems.append(
                f"{name} isn't in this MailStudio: it was deleted, or uploaded to another install. "
                "Upload it again."
            )
            continue
        if asset:
            seen_assets[asset["id"]] = asset

        if urlsplit(image.src).scheme == "http":
            warnings.append(f"{name} uses plain http. Some mail apps only load https images.")
        alt = (image.alt or "").strip()
        if image.background:
            pass  # the words on top of it say what it's about
        elif not alt:
            warnings.append(
                f"{name} has no description. It's shown when images are blocked "
                "and read out by screen readers."
            )
        elif alt.lower() in PLACEHOLDER_ALTS or _FILE_NAME.match(alt):
            warnings.append(f"{name} is described as “{alt}”. Say what it shows instead, in a few words.")
        if asset and image.width and asset["width"] < image.width:
            warnings.append(
                f"{name} is {asset['width']}px wide but shown at {image.width}px, so it will look blurry. "
                f"Upload one at least {image.width * 2}px wide for sharp screens."
            )
        if asset and asset["bytes"] > HEAVY_IMAGE_BYTES:
            warnings.append(
                f"{name} is {asset['bytes'] // 1024} KB, slow on mobile data. "
                "A smaller or simpler image would load faster."
            )

    total = sum(a["bytes"] for a in seen_assets.values())
    if total > HEAVY_EMAIL_BYTES:
        warnings.append(
            f"The images add up to {total / 1024 / 1024:.1f} MB. Keep an email's images under about 1 MB "
            "so it opens quickly on phones."
        )
    if images and len(text.split()) < MIN_WORDS_WITH_IMAGES:
        warnings.append(
            "This email is mostly images. Spam filters distrust image-only emails, and people with images "
            "turned off see very little: add some text."
        )
    return Report(problems, warnings)
