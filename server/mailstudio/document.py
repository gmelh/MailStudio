"""The campaign document: brand + ordered sections, as written by the editor (schema 2).

Pydantic checks the shape; `check_sections` then checks every section against the
shared section library (known type, known fields, sensible values). The JSON
Schema in `schema/campaign.schema.json` is generated from these models.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .errors import ProblemError
from .sections import Library

MAX_SECTIONS = 300
MAX_SETTING = 20_000

Colour = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
FontStack = Literal[
    "Arial, Helvetica, sans-serif",
    "Georgia, serif",
    "Verdana, Geneva, sans-serif",
    "Tahoma, Geneva, sans-serif",
    '"Trebuchet MS", Arial, sans-serif',
    '"Times New Roman", Times, serif',
]
FONT_STACKS: tuple[str, ...] = get_args(FontStack)
DEFAULT_FONT: FontStack = "Arial, Helvetica, sans-serif"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Palette(_Model):
    outer: Colour = "#e9edf4"
    background: Colour = "#ffffff"
    featured: Colour = "#eaf0fc"
    text: Colour = "#4e5b70"
    heading: Colour = "#273449"
    link: Colour = "#4768da"
    accent: Colour = "#4768da"
    buttonText: Colour = "#ffffff"


def _dark() -> Palette:
    return Palette(
        outer="#10151f",
        background="#171c26",
        featured="#26344a",
        text="#d0d9e6",
        heading="#f1f5fa",
        link="#a8c4ff",
        accent="#7698ef",
        buttonText="#ffffff",
    )


class Fonts(_Model):
    heading: FontStack = DEFAULT_FONT
    body: FontStack = DEFAULT_FONT
    small: FontStack = DEFAULT_FONT


class ButtonStyle(_Model):
    bold: bool = True
    italic: bool = False


class Brand(_Model):
    width: Annotated[int, Field(ge=400, le=900)] = 600
    padding: Annotated[int, Field(ge=8, le=70)] = 32
    radius: Annotated[int, Field(ge=0, le=32)] = 6
    fontSize: Annotated[int, Field(ge=12, le=24)] = 16
    fonts: Fonts = Fonts()
    button: ButtonStyle = ButtonStyle()
    logo: Annotated[str, Field(max_length=2000)] = ""
    light: Palette = Palette()
    dark: Palette = Field(default_factory=_dark)


class Section(_Model):
    id: Annotated[str, Field(min_length=1, max_length=64)]
    type: Annotated[str, Field(min_length=1, max_length=40)]
    settings: dict[str, str] = {}


class Document(BaseModel):
    """`name`, `subject` and `id` from an editor export are accepted and ignored:
    those belong to the campaign, not its design."""

    model_config = ConfigDict(extra="ignore")

    schema_: Literal[2] = Field(2, alias="schema")
    brand: Brand = Brand()
    sections: Annotated[list[Section], Field(max_length=MAX_SECTIONS)] = []

    def dump(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


def check_sections(document: Document, library: Library) -> None:
    """Reject unknown section types, unknown fields, over-long values, duplicate
    IDs and embedded (data:) images, listing every problem at once."""
    errors: list[dict[str, str]] = []
    seen: set[str] = set()

    def error(index: int, message: str, key: str | None = None) -> None:
        location = f"document.sections.{index}" + (f".settings.{key}" if key else "")
        errors.append({"location": location, "message": message})

    if document.brand.logo.startswith("data:"):
        errors.append(
            {"location": "document.brand.logo", "message": "Use a hosted image URL, not an embedded image"}
        )

    for i, section in enumerate(document.sections):
        if section.id in seen:
            error(i, f"Duplicate section id {section.id!r}")
        seen.add(section.id)
        definition = library.sections.get(section.type)
        if definition is None:
            error(i, f"Unknown section type {section.type!r}")
            continue
        known = {f.key: f for f in definition.fields}
        for key, value in section.settings.items():
            field = known.get(key)
            if field is None:
                error(i, f"Unknown field for a {definition.label} section", key)
            elif len(value) > MAX_SETTING:
                error(i, f"Longer than {MAX_SETTING:,} characters", key)
            elif field.kind == "image" and value.startswith("data:"):
                error(i, "Use a hosted image URL, not an embedded image", key)

    if errors:
        raise ProblemError(
            422,
            "validation",
            "The campaign document is not valid",
            "See `errors` for each problem.",
            errors=errors,
        )
