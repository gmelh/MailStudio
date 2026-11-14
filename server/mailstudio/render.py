"""Turns a campaign document into email HTML and plain text.

    document + brand ──► section templates (Mustache) ──► MJML ──► mrml ──► HTML
                                                                       └──► plain text

Rendering happens once per campaign. The result still contains the owner's merge
tags (`{{first_name|there}}`, `{{unsubscribe_url}}`); `personalise` fills them in
for each recipient. Merge tags are never evaluated as templates, so neither an
owner's content nor a subscriber's data can run code.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import chevron
import mrml

from .document import Brand, Document
from .sections import FieldDef, Library

MERGE_TAG = re.compile(r"\{\{\s*([a-z][a-z0-9_]{0,39})\s*(?:\|([^{}]{0,200}))?\}\}")
_MERGE_LITERALS = {"unsubscribe_url": "{{unsubscribe_url}}"}
_COLOR_SCHEME_META = (
    '<meta name="color-scheme" content="light dark">'
    '<meta name="supported-color-schemes" content="light dark">'
)


@dataclass(frozen=True, slots=True)
class Business:
    name: str
    address: str


@dataclass(frozen=True, slots=True)
class Rendered:
    subject: str
    html: str
    text: str


# ---------------------------------------------------------------------------
# Preparing template values


def safe_url(value: str) -> str:
    value = value.strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    return value if parts.scheme in ("http", "https") and parts.netloc else ""


_LINK = re.compile(r"\[([^\]\n]{1,300})\]\(([^)\s]{1,2000})\)")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<![\w*])[*_](?=\S)(.+?)(?<=\S)[*_](?![\w*])")
_LIST_ITEM = re.compile(r"^\s*[-*]\s+")


def _emphasis(text: str) -> str:
    escaped = html.escape(text, quote=False)
    return _ITALIC.sub(r"<em>\1</em>", _BOLD.sub(r"<strong>\1</strong>", escaped))


def inline_html(text: str, link_color: str) -> str:
    """Escape text, then allow a small Markdown subset: **bold**, *italic* or
    _italic_, and [label](https://link). Anything else stays plain text."""
    out, position = [], 0
    for match in _LINK.finditer(text):
        out.append(_emphasis(text[position : match.start()]))
        url = safe_url(match.group(2))
        if url:
            out.append(
                f'<a class="ms-link" href="{html.escape(url)}" style="color:{link_color}">'
                f"{_emphasis(match.group(1))}</a>"
            )
        else:
            out.append(_emphasis(match.group(0)))
        position = match.end()
    out.append(_emphasis(text[position:]))
    return "".join(out)


def paragraphs_html(text: str, link_color: str = "#4768da") -> str:
    """Blank lines separate paragraphs; a paragraph whose lines all start with
    "- " becomes a bulleted list; single line breaks are kept."""
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", text) if b.strip()]
    out = []
    for i, block in enumerate(blocks):
        margin = "0" if i == len(blocks) - 1 else "0 0 1em"
        lines = block.split("\n")
        if all(_LIST_ITEM.match(line) for line in lines):
            items = "".join(f"<li>{inline_html(_LIST_ITEM.sub('', line), link_color)}</li>" for line in lines)
            out.append(f'<ul style="margin:{margin};padding-left:22px">{items}</ul>')
        else:
            joined = "<br>".join(inline_html(line.strip(), link_color) for line in lines)
            out.append(f'<p style="margin:{margin}">{joined}</p>')
    return "".join(out)


def _number(value: str, field: FieldDef, fallback: str) -> str:
    try:
        n = int(float(value))
    except ValueError:
        n = int(fallback)
    if field.min is not None:
        n = max(field.min, n)
    if field.max is not None:
        n = min(field.max, n)
    return str(n)


def prepare_settings(
    fields: tuple[FieldDef, ...], defaults: dict[str, str], settings: dict[str, str], brand: dict[str, Any]
) -> dict[str, Any]:
    """Derive the values a section template may use from its raw settings."""
    out: dict[str, Any] = {}
    for f in fields:
        value = settings.get(f.key, defaults[f.key])
        if f.kind in ("url", "image"):
            if not value and f.fallback == "brand.logo":
                value = brand["logo"]
            value = safe_url(value)
            out[f"{f.key}_ok"] = bool(value)
        elif f.kind == "number":
            value = _number(value, f, defaults[f.key])
        elif f.kind == "select":
            if value not in f.options:
                value = defaults[f.key]
            for option in f.options:
                out[f"{f.key}_is_{option}"] = value == option
        elif f.kind == "textarea":
            out[f"{f.key}_html"] = paragraphs_html(value, brand["link"])
            out[f"{f.key}_lines"] = [line.strip() for line in value.split("\n") if line.strip()]
        out[f.key] = value
    return out


def _blend(fg: str, bg: str, amount: float) -> str:
    """Mix `amount` of colour fg over bg (both #rrggbb)."""
    channels = []
    for i in (1, 3, 5):
        f, b = int(fg[i : i + 2], 16), int(bg[i : i + 2], 16)
        channels.append(round(f * amount + b * (1 - amount)))
    return "#" + "".join(f"{c:02x}" for c in channels)


def brand_values(brand: Brand, scheme: str = "light") -> dict[str, Any]:
    """Template values for the brand. `scheme="dark"` renders the dark palette as the
    base (used for previews); normal renders use the light palette and carry the dark
    one as a `prefers-color-scheme: dark` style block."""
    p = brand.dark if scheme == "dark" else brand.light
    # Font stacks go inside double-quoted attributes, so their quotes become single quotes.
    fonts = {k: v.replace('"', "'") for k, v in brand.fonts.model_dump().items()}
    heading = f"margin:0 0 12px;font-family:{fonts['heading']};color:{p.heading}"
    return {
        **p.model_dump(),
        "width": brand.width,
        "padding": brand.padding,
        "radius": brand.radius,
        "buttonRadius": min(brand.radius + 1, 24),
        "fontSize": brand.fontSize,
        "fonts": fonts,
        "buttonWeight": "700" if brand.button.bold else "400",
        "buttonStyle": "italic" if brand.button.italic else "normal",
        "logo": brand.logo,
        "muted": _blend(p.text, p.background, 0.8),
        "divider": _blend(p.text, p.background, 0.2),
        "h2": f"{heading};font-size:30px;line-height:1.16;letter-spacing:-0.5px",
        "h3": f"{heading};font-size:21px;line-height:1.3",
        "small": f"font-family:{fonts['small']};font-size:12px;font-weight:400;letter-spacing:0;"
        f"color:{p.text}",
    }


def dark_css(brand: Brand) -> str:
    """Overrides that switch an email to the brand's dark palette in mail apps that
    honour `prefers-color-scheme` (Apple Mail, Outlook for Mac and iOS, many others),
    plus the attribute selectors Outlook.com and the Outlook apps add in dark mode.
    Gmail ignores both and darkens emails its own way."""
    d = brand.dark
    muted = _blend(d.text, d.background, 0.8)
    divider = _blend(d.text, d.background, 0.2)
    rules = [
        ("body, .ms-outer", f"background:{d.outer} !important;background-color:{d.outer} !important"),
        (
            ".ms-body, .ms-body > table",
            f"background:{d.background} !important;background-color:{d.background} !important",
        ),
        (
            ".ms-featured, .ms-featured > table",
            f"background:{d.featured} !important;background-color:{d.featured} !important",
        ),
        (".ms-text div, .ms-text p, .ms-text li", f"color:{d.text} !important"),
        (".ms-title div, .ms-heading", f"color:{d.heading} !important"),
        (".ms-muted div", f"color:{muted} !important"),
        (".ms-accent div", f"color:{d.accent} !important"),
        (".ms-link", f"color:{d.link} !important"),
        (
            ".ms-button td, .ms-button a",
            f"background:{d.accent} !important;background-color:{d.accent} !important",
        ),
        (".ms-button a", f"color:{d.buttonText} !important"),
        (".ms-divider p", f"border-top-color:{divider} !important"),
    ]
    media = "".join(f"{selector}{{{body}}}" for selector, body in rules)
    outlook = "".join(
        f"{', '.join(f'[data-ogsc] {part.strip()}' for part in selector.split(','))}{{{body}}}"
        for selector, body in rules
    )
    return f"@media (prefers-color-scheme: dark){{{media}}}{outlook}"


# ---------------------------------------------------------------------------
# Rendering


def css_id(section_id: str) -> str:
    """A section ID made safe for use in a CSS class name."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", section_id)[:64]


def to_mjml(
    document: Document,
    library: Library,
    *,
    subject: str,
    preheader: str,
    business: Business,
    scheme: str = "light",
) -> str:
    brand = brand_values(document.brand, scheme)
    parts = []
    for section in document.sections:
        definition = library.sections[section.type]
        context = {
            "section_id": css_id(section.id),
            "s": prepare_settings(definition.fields, definition.defaults, section.settings, brand),
            "brand": brand,
            "merge": _MERGE_LITERALS,
        }
        parts.append(chevron.render(definition.template, context))

    has_unsubscribe = any(library.sections[s.type].provides_unsubscribe for s in document.sections)
    compliance = (
        ""
        if has_unsubscribe
        else chevron.render(
            library.compliance, {"brand": brand, "merge": _MERGE_LITERALS, "business": business}
        )
    )
    page: str = chevron.render(
        library.document,
        {
            "subject": subject,
            "preheader": preheader,
            "brand": brand,
            "sections": "\n".join(parts),
            "compliance": compliance,
            "dark_css": dark_css(document.brand) if scheme == "light" else "",
        },
    )
    return page


def render(
    document: Document,
    library: Library,
    *,
    subject: str,
    preheader: str,
    business: Business,
    scheme: str = "light",
) -> Rendered:
    mjml = to_mjml(document, library, subject=subject, preheader=preheader, business=business, scheme=scheme)
    content: str = mrml.to_html(mjml).content
    if scheme == "light":
        # Tells Apple Mail and others the email has its own dark styles. mrml drops raw
        # tags in <mj-head>, so these go in after rendering.
        content = content.replace("<head>", f"<head>{_COLOR_SCHEME_META}", 1)
    return Rendered(subject=subject, html=content, text=html_to_text(content))


# ---------------------------------------------------------------------------
# Plain text


class _TextExtractor(HTMLParser):
    _BLOCK = frozenset({"p", "div", "h1", "h2", "h3", "li", "tr", "table"})
    _SKIP = frozenset({"style", "script", "head", "title"})
    _VOID = frozenset({"br", "img", "meta", "link", "hr", "input", "col", "area", "base", "source"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden: list[bool] = []  # one entry per open element: is it (or an ancestor) hidden?
        self._href: list[str | None] = []

    @property
    def _skipping(self) -> bool:
        return bool(self._hidden) and self._hidden[-1]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "br":
            self.parts.append("\n")
            return
        if tag in self._VOID:
            return
        style = (attributes.get("style") or "").replace(" ", "").lower()
        self._hidden.append(self._skipping or tag in self._SKIP or "display:none" in style)
        if self._skipping:
            return
        if tag == "a":
            self._href.append(attributes.get("href"))
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._VOID:
            return
        was_skipping = self._skipping
        if self._hidden:
            self._hidden.pop()
        if was_skipping:
            return
        if tag == "a" and self._href:
            href = self._href.pop()
            if href and href.startswith(("http", "{{")):
                self.parts.append(f" ({href})")
        elif tag in self._BLOCK and tag != "li":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    """A readable plain-text version: paragraphs, list bullets, and link URLs in brackets."""
    parser = _TextExtractor()
    parser.feed(markup)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    text = "\n".join(lines)
    text = re.sub(r"(\n *){3,}", "\n\n", text).strip()
    return text.replace("\N{NO-BREAK SPACE}", " ") + "\n"


# ---------------------------------------------------------------------------
# Per-recipient merge tags


def personalise(content: str, values: dict[str, str], *, escape: bool) -> str:
    """Fill `{{name}}` / `{{name|fallback}}` in one pass. Unknown or empty names use
    the fallback (or nothing). Values are HTML-escaped when `escape` is set, and are
    never themselves scanned for tags."""

    def replace(match: re.Match[str]) -> str:
        value = values.get(match.group(1)) or (match.group(2) or "").strip()
        return html.escape(value) if escape else value

    return MERGE_TAG.sub(replace, content)


def merge_values(contact: dict[str, Any], unsubscribe_url: str) -> dict[str, str]:
    values = {k: str(v) for k, v in contact.get("fields", {}).items()}
    values.update(
        email=contact["email"],
        first_name=contact.get("first_name", ""),
        last_name=contact.get("last_name", ""),
        unsubscribe_url=unsubscribe_url,
    )
    return values
