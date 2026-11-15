"""Templates: whole designed emails people start a campaign from.

A template is one JSON file (see `Template`): a name, a short description, a category, an
optional subject and preview text, and a campaign document — the brand (colours, fonts,
shape) and the sections with their words.

Templates come from two places:

* bundled with the app, in `sections/gallery/*.json`, so a fresh install has a few;
* the online gallery at mailstudio.email (or any address in MAILSTUDIO_GALLERY_URL): an
  `index.json` listing templates, each a JSON file next to it. The server fetches them,
  so the browser never talks to the gallery and nothing but JSON is ever taken from it.

When someone uses a template they choose whose colours it has: the template's own, or the
light and dark palettes from their brand in Settings. Their logo and business details always
come from Settings.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .document import Brand, Document
from .errors import ProblemError

FORMAT = "mailstudio-template"
DEFAULT_GALLERY = "https://mailstudio.email/gallery/index.json"
MAX_TEMPLATE_BYTES = 512 * 1024
MAX_INDEX_BYTES = 1024 * 1024
CATEGORIES = ("newsletter", "promotion", "event", "announcement", "welcome", "other")

Category = Literal["newsletter", "promotion", "event", "announcement", "welcome", "other"]
Colours = Literal["template", "brand"]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]


class Template(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    format: Literal["mailstudio-template"] = "mailstudio-template"
    version: Literal[1] = 1
    id: Slug
    name: Annotated[str, Field(min_length=1, max_length=60)]
    description: Annotated[str, Field(max_length=300)] = ""
    category: Category = "other"
    tags: Annotated[list[Annotated[str, Field(max_length=30)]], Field(max_length=10)] = []
    author: Annotated[str, Field(max_length=100)] = ""
    subject: Annotated[str, Field(max_length=300)] = ""
    preheader: Annotated[str, Field(max_length=300)] = ""
    document: Document

    def summary(self) -> dict[str, Any]:
        """What a picker shows, plus the document to preview it with."""
        return self.model_dump(mode="json", by_alias=True)


def parse(raw: Any) -> Template:
    """A template from decoded JSON, or a 422 listing what's wrong with it."""
    if isinstance(raw, dict) and raw.get("format") not in (None, FORMAT):
        raise ProblemError(
            422, "not-a-template", "Not a MailStudio template", "The file's format isn't mailstudio-template."
        )
    try:
        return Template.model_validate(raw)
    except ValidationError as exc:
        raise ProblemError(
            422,
            "invalid-template",
            "The template is not valid",
            "See `errors` for each problem.",
            errors=[
                {"location": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()
            ],
        ) from None


@cache
def bundled(directory: Path) -> tuple[Template, ...]:
    """The templates shipped in `sections/gallery`, in file-name order (01-…, 02-…)."""
    folder = directory / "gallery"
    if not folder.is_dir():
        return ()
    return tuple(
        parse(json.loads(path.read_text(encoding="utf-8"))) for path in sorted(folder.glob("*.json"))
    )


def find_bundled(directory: Path, template_id: str) -> Template | None:
    return next((t for t in bundled(directory) if t.id == template_id), None)


def apply(
    template: Template, brand: Brand, business_name: str, business_address: str, colours: Colours
) -> Document:
    """The campaign document a template starts: the template's design with the owner's logo,
    business details and — if they choose — their brand colours."""
    style = template.document.brand
    updates: dict[str, Any] = {"logo": brand.logo or style.logo, "webLink": brand.webLink}
    if colours == "brand":
        updates |= {"light": brand.light, "dark": brand.dark}
    document = template.document.model_copy(deep=True)
    document.brand = style.model_copy(update=updates)
    name = business_name or "Your Company"
    for section in document.sections:
        s = section.settings
        if section.type == "header" and not s.get("brand"):
            s["brand"] = name.upper()
        elif section.type == "footer":
            if not s.get("company"):
                s["company"] = name
            if not s.get("address"):
                s["address"] = business_address or "Your business address"
    return document


def from_campaign(campaign: dict[str, Any], document: Document) -> Template:
    """A campaign saved as a template file, to share or to send to the gallery. The logo and
    the business details stay behind: whoever uses the template brings their own."""
    copy = document.model_copy(deep=True)
    copy.brand = copy.brand.model_copy(update={"logo": ""})
    for section in copy.sections:
        if section.type == "footer":
            section.settings.pop("company", None)
            section.settings.pop("address", None)
        elif section.type == "header":
            section.settings.pop("brand", None)
    name = (campaign.get("name") or "My template")[:60]
    slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return Template(
        id=(slug or "template")[:63],
        name=name,
        subject=campaign.get("subject") or "",
        preheader=campaign.get("preheader") or "",
        document=copy,
    )


# The online gallery ---------------------------------------------------------------------

_cache: dict[str, tuple[float, Any]] = {}
CACHE_SECONDS = 600


class GalleryEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Slug
    name: Annotated[str, Field(min_length=1, max_length=60)]
    description: Annotated[str, Field(max_length=300)] = ""
    category: Category = "other"
    tags: list[str] = []
    author: str = ""
    file: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*\.json$", max_length=200)]
    preview: Annotated[str, Field(max_length=200)] = ""

    @field_validator("file")
    @classmethod
    def _inside_the_gallery(cls, value: str) -> str:
        if ".." in value.split("/"):
            raise ValueError("must be a path inside the gallery")
        return value


class GalleryIndex(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: Literal["mailstudio-gallery"]
    version: Literal[1] = 1
    templates: list[GalleryEntry] = []


def _fetch(url: str, limit: int) -> Any:
    if not url.startswith("https://") and not url.startswith("http://localhost"):
        raise ProblemError(
            502, "gallery-unreachable", "The gallery isn't available", "The gallery address must use https."
        )
    cached = _cache.get(url)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "MailStudio"})
    try:
        with urllib.request.urlopen(request, timeout=8) as reply:
            raw = reply.read(limit + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ProblemError(
            502, "gallery-unreachable", "The gallery isn't available", f"Couldn't reach {url}: {reason}"
        ) from None
    if len(raw) > limit:
        raise ProblemError(
            502, "gallery-invalid", "The gallery sent too much", f"{url} is larger than expected."
        )
    try:
        data = json.loads(raw)
    except ValueError:
        raise ProblemError(
            502, "gallery-invalid", "The gallery sent something unexpected", f"{url} isn't JSON."
        ) from None
    _cache[url] = (time.monotonic(), data)
    return data


def gallery_index(url: str) -> GalleryIndex:
    try:
        return GalleryIndex.model_validate(_fetch(url, MAX_INDEX_BYTES))
    except ValidationError:
        raise ProblemError(
            502,
            "gallery-invalid",
            "The gallery sent something unexpected",
            f"{url} isn't a template gallery.",
        ) from None


def gallery_template(url: str, template_id: str) -> Template:
    """A template from the gallery, found through the index so only files it lists are read."""
    entry = next((t for t in gallery_index(url).templates if t.id == template_id), None)
    if entry is None:
        raise ProblemError(
            404, "not-found", "Template not found", f"The gallery has no template {template_id!r}."
        )
    return parse(_fetch(urllib.parse.urljoin(url, entry.file), MAX_TEMPLATE_BYTES))


def gallery_previews(url: str, entries: list[GalleryEntry]) -> list[dict[str, Any]]:
    """Index entries for the picker, with preview addresses made absolute."""
    out = []
    for entry in entries:
        item = entry.model_dump(exclude={"file"})
        item["preview"] = urllib.parse.urljoin(url, entry.preview) if entry.preview else ""
        out.append(item)
    return out
