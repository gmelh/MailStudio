"""Loads the shared section definitions (`sections/sections.json` + MJML templates)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

FieldKind = Literal["text", "textarea", "url", "image", "number", "select"]


@dataclass(frozen=True, slots=True)
class FieldDef:
    key: str
    label: str
    kind: FieldKind
    min: int | None = None
    max: int | None = None
    options: tuple[str, ...] = ()
    fallback: str | None = None


@dataclass(frozen=True, slots=True)
class SectionDef:
    type: str
    label: str
    fields: tuple[FieldDef, ...]
    defaults: dict[str, str]
    template: str
    provides_unsubscribe: bool


@dataclass(frozen=True, slots=True)
class Library:
    sections: dict[str, SectionDef]
    document: str
    compliance: str
    manifest: dict[str, Any]
    """The raw sections.json, as served to the editor."""
    presets: dict[str, Any]


def _field(raw: dict[str, Any]) -> FieldDef:
    return FieldDef(
        key=raw["key"],
        label=raw["label"],
        kind=raw["kind"],
        min=raw.get("min"),
        max=raw.get("max"),
        options=tuple(value for value, _ in raw.get("options", [])),
        fallback=raw.get("fallback"),
    )


@cache
def load(directory: Path) -> Library:
    manifest = json.loads((directory / "sections.json").read_text(encoding="utf-8"))
    templates = directory / "templates"
    sections = {}
    for type_, raw in manifest["sections"].items():
        fields = tuple(_field(f) for f in raw["fields"])
        if set(raw["defaults"]) != {f.key for f in fields}:
            raise ValueError(f"Section {type_!r}: defaults must cover exactly its fields")
        sections[type_] = SectionDef(
            type=type_,
            label=raw["label"],
            fields=fields,
            defaults=dict(raw["defaults"]),
            template=(templates / f"{type_}.mjml").read_text(encoding="utf-8"),
            provides_unsubscribe=bool(raw.get("providesUnsubscribe")),
        )
    presets_file = directory / "presets.json"
    presets = json.loads(presets_file.read_text(encoding="utf-8"))["presets"] if presets_file.exists() else {}
    return Library(
        sections=sections,
        document=(templates / "_document.mjml").read_text(encoding="utf-8"),
        compliance=(templates / "_compliance.mjml").read_text(encoding="utf-8"),
        manifest=manifest,
        presets=presets,
    )
