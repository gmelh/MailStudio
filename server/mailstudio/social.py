"""Social icons for the Social Links section, coloured to suit each email.

The shapes live in sections/social (white on transparency, made from Font Awesome Free,
CC BY 4.0). An email asks for /social/<network>-<style>-<colour>.png, and MailStudio makes
that icon once with Pillow and keeps it, so icons come from the sender's own install (no
third-party image hosts) in any colour:

- circle: the logo on a circle of that colour (the logo turns dark on light colours);
- plain:  the logo itself in that colour.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw

# The network's own colour, for "Each network's colour". The website uses the brand accent.
NETWORKS: dict[str, tuple[str, str]] = {
    "website": ("Website", ""),
    "instagram": ("Instagram", "#E4405F"),
    "facebook": ("Facebook", "#1877F2"),
    "linkedin": ("LinkedIn", "#0A66C2"),
    "x": ("X", "#000000"),
    "youtube": ("YouTube", "#FF0000"),
    "tiktok": ("TikTok", "#000000"),
    "pinterest": ("Pinterest", "#BD081C"),
    "threads": ("Threads", "#000000"),
    "bluesky": ("Bluesky", "#0085FF"),
    "whatsapp": ("WhatsApp", "#25D366"),
    "mastodon": ("Mastodon", "#6364FF"),
}
STYLES = ("circle", "plain")
SIZE = 120  # shown at up to 40px: sharp on high-density screens
NAME = re.compile(r"^(" + "|".join(NETWORKS) + r")-(circle|plain)-([0-9a-f]{6})$")


def _dark(hex_colour: str) -> bool:
    r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.6


def draw(shape: Image.Image, style: str, hex_colour: str) -> Image.Image:
    colour = tuple(int(hex_colour[i : i + 2], 16) for i in (0, 2, 4))
    mask = shape.getchannel("A")
    if style == "plain":  # a little smaller than the circle, so the two look balanced
        inner = round(SIZE * 0.8)
        logo = Image.new("RGBA", (inner, inner), (*colour, 0))
        logo.putalpha(mask.resize((inner, inner), Image.Resampling.LANCZOS))
        icon = Image.new("RGBA", (SIZE, SIZE), (*colour, 0))
        icon.alpha_composite(logo, ((SIZE - inner) // 2, (SIZE - inner) // 2))
        return icon
    big = SIZE * 4  # draw large, then shrink, for a smooth edge
    icon = Image.new("RGBA", (big, big), (*colour, 0))
    circle = Image.new("L", (big, big), 0)
    ImageDraw.Draw(circle).ellipse((0, 0, big - 1, big - 1), fill=255)
    icon.putalpha(circle)
    logo_size = round(big * 0.52)
    logo = Image.new(
        "RGBA", (logo_size, logo_size), (255, 255, 255, 0) if _dark(hex_colour) else (17, 24, 39, 0)
    )
    logo.putalpha(mask.resize((logo_size, logo_size), Image.Resampling.LANCZOS))
    offset = (big - logo_size) // 2
    icon.alpha_composite(logo, (offset, offset))
    return icon.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def icon_file(name: str, shapes: Path, cache: Path) -> Path | None:
    """The PNG for "<network>-<style>-<rrggbb>", made on first use. None if unknown."""
    match = NAME.match(name)
    if not match:
        return None
    network, style, hex_colour = match.groups()
    path = cache / f"{name}.png"
    if not path.is_file():
        source = shapes / f"{network}.png"
        if not source.is_file():
            return None
        cache.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as shape:
            image = draw(shape.convert("RGBA"), style, hex_colour)
        temporary = path.with_suffix(".tmp")
        image.save(temporary, "PNG", optimize=True)
        temporary.replace(path)  # never serve a half-written file
    return path
