"""Request and response models. Validation rules for contacts, tags and custom
fields live here, so the API and CSV import apply exactly the same rules."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    IPvAnyAddress,
    field_validator,
)

from .document import Document
from .gallery import Colours, Template

MAX_TAGS = 50
MAX_FIELDS = 50
MAX_FIELD_VALUE = 1000
RESERVED_FIELDS = frozenset({"email", "first_name", "last_name", "unsubscribe_url"})

_TAG = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,49}$")
_FIELD_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def normalise_email(email: str) -> str:
    return email.strip().lower()


def normalise_tag(tag: str) -> str:
    cleaned = " ".join(tag.strip().lower().split())
    if not _TAG.match(cleaned):
        raise ValueError(f"Invalid tag {tag!r}: use 1-50 letters, digits, spaces, hyphens or underscores")
    return cleaned


def validate_tags(values: list[str]) -> list[str]:
    tags = sorted({normalise_tag(v) for v in values})
    if len(tags) > MAX_TAGS:
        raise ValueError(f"At most {MAX_TAGS} tags per contact")
    return tags


FieldValue = str | int | float | bool


def validate_fields(values: dict[str, FieldValue]) -> dict[str, FieldValue]:
    if len(values) > MAX_FIELDS:
        raise ValueError(f"At most {MAX_FIELDS} custom fields per contact")
    for key, value in values.items():
        if not _FIELD_KEY.match(key):
            raise ValueError(f"Invalid field name {key!r}: use lower-case letters, digits and underscores")
        if key in RESERVED_FIELDS:
            raise ValueError(f"{key!r} is a built-in field and can't be used as a custom field")
        if isinstance(value, str) and len(value) > MAX_FIELD_VALUE:
            raise ValueError(f"Field {key!r} is longer than {MAX_FIELD_VALUE} characters")
    return values


Tags = Annotated[list[str], AfterValidator(validate_tags)]
Fields = Annotated[dict[str, FieldValue], AfterValidator(validate_fields)]
Name = Annotated[str, Field(max_length=200)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Consent


class ConsentIn(Strict):
    """How permission to email this person was obtained. Required whenever an
    address is added to a list, so every subscription has a recorded source."""

    source: Annotated[str, Field(min_length=1, max_length=200, examples=["Signed up at the shop counter"])]
    ip: IPvAnyAddress | None = None


# ---------------------------------------------------------------------------
# Contacts


class ContactCreate(Strict):
    email: EmailStr
    first_name: Name = ""
    last_name: Name = ""
    fields: Fields = {}
    tags: Tags = []
    lists: list[str] = Field(default=[], description="IDs of lists to subscribe the contact to")
    consent: ConsentIn | None = Field(default=None, description="Required when `lists` is not empty")


class ContactUpdate(Strict):
    """Only the fields present are changed. `fields` and `tags` replace the existing values."""

    email: EmailStr | None = None
    first_name: Name | None = None
    last_name: Name | None = None
    fields: Fields | None = None
    tags: Tags | None = None


class Membership(BaseModel):
    list_id: str
    status: Literal["pending", "subscribed", "unsubscribed"]


class Contact(BaseModel):
    id: str
    email: str
    first_name: str
    last_name: str
    fields: dict[str, FieldValue]
    tags: list[str]
    lists: list[Membership]
    suppressed: Literal["bounce", "complaint", "manual"] | None
    created_at: str
    updated_at: str


class ConsentEvent(BaseModel):
    action: Literal["consent", "confirm", "unsubscribe"]
    source: str
    ip: str | None
    list_id: str | None
    list_name: str | None
    created_at: str


class ContactPage(BaseModel):
    data: list[Contact]
    next_cursor: str | None


# ---------------------------------------------------------------------------
# Lists


class SignupForm(Strict):
    """The list's public signup form, at /f/{list_id} and as embeddable HTML."""

    enabled: bool = False
    title: Annotated[str, Field(max_length=200, description="Defaults to the list's name")] = ""
    intro: Annotated[str, Field(max_length=1000)] = ""
    button: Annotated[str, Field(min_length=1, max_length=60)] = "Subscribe"
    ask_name: bool = Field(True, description="Ask for first and last name as well as email")
    thanks: Annotated[str, Field(max_length=1000, description="Shown after signing up")] = ""
    redirect_url: Annotated[str, Field(max_length=500, description="Go here after signing up instead")] = ""

    @field_validator("redirect_url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        value = value.strip()
        if value and not value.lower().startswith(("https://", "http://")):
            raise ValueError("must start with https://")
        return value


class ListCreate(Strict):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(max_length=500)] = ""
    double_opt_in: bool = Field(
        default=True, description="Public signups must confirm by email before they're subscribed"
    )


class ListUpdate(Strict):
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    description: Annotated[str, Field(max_length=500)] | None = None
    double_opt_in: bool | None = None
    signup: SignupForm | None = None


class ListCounts(BaseModel):
    subscribed: int
    pending: int
    unsubscribed: int


class MailingList(BaseModel):
    id: str
    name: str
    description: str
    double_opt_in: bool
    signup: SignupForm
    counts: ListCounts
    created_at: str
    updated_at: str


class ListPage(BaseModel):
    data: list[MailingList]
    next_cursor: str | None


class MembersAdd(Strict):
    contact_ids: Annotated[list[str], Field(min_length=1, max_length=1000)]
    consent: ConsentIn
    resubscribe: bool = Field(
        default=False,
        description="Re-add contacts who previously unsubscribed from this list. Only set this with fresh, "
        "explicit consent from each person.",
    )


class MembersAddResult(BaseModel):
    added: list[str]
    already_subscribed: list[str]
    skipped_unsubscribed: list[str]
    not_found: list[str]


class Member(BaseModel):
    id: str
    """The contact ID."""
    email: str
    first_name: str
    last_name: str
    status: Literal["pending", "subscribed", "unsubscribed"]
    subscribed_at: str | None
    unsubscribed_at: str | None


class MemberPage(BaseModel):
    data: list[Member]
    next_cursor: str | None


class Unsubscribe(Strict):
    source: Annotated[str, Field(min_length=1, max_length=200)] = "API"


# ---------------------------------------------------------------------------
# Suppressions


class SuppressionCreate(Strict):
    email: EmailStr
    detail: Annotated[str, Field(max_length=500)] = ""


class Suppression(BaseModel):
    id: str
    """The suppressed address (suppressions are keyed by address)."""
    email: str
    reason: Literal["bounce", "complaint", "manual"]
    detail: str
    created_at: str


class SuppressionPage(BaseModel):
    data: list[Suppression]
    next_cursor: str | None


# ---------------------------------------------------------------------------
# Import


class ImportProblem(BaseModel):
    row: int
    """1-based CSV line number, counting the header as line 1."""
    error: str


class ImportResult(BaseModel):
    rows: int
    created: int
    updated: int
    added_to_list: int
    skipped_unsubscribed: int
    skipped_suppressed: int
    invalid: int
    problems: list[ImportProblem]
    """The first 100 invalid rows."""


# ---------------------------------------------------------------------------
# Campaigns

CampaignStatus = Literal["draft", "scheduled", "sending", "paused", "sent", "cancelled"]
Text300 = Annotated[str, Field(max_length=300)]


class CampaignCreate(Strict):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    subject: Text300 = ""
    preheader: Text300 = Field("", description="Preview text shown after the subject in most inboxes")
    from_name: Name | None = Field(None, description="Defaults to the sender in Settings")
    from_email: EmailStr | None = Field(None, description="Defaults to the sender in Settings")
    reply_to: EmailStr | None = Field(None, description="Defaults to the sender in Settings")
    lists: list[str] = Field(default=[], description="Send to subscribed members of any of these lists")
    tags: Tags = Field(default=[], description="If set, only contacts with at least one of these tags")
    document: Document | None = Field(
        None, description="Defaults to a starter email in the brand from Settings"
    )
    template: Template | None = Field(
        None, description="Start from this template (ignored if `document` is given)"
    )
    template_id: str | None = Field(None, description="Start from a template bundled with the app, by its id")
    colours: Colours = Field(
        "template", description="Use the template's colours, or the brand colours from Settings"
    )


class CampaignUpdate(Strict):
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    subject: Text300 | None = None
    preheader: Text300 | None = None
    from_name: Name | None = None
    from_email: EmailStr | None = None
    reply_to: EmailStr | None = None
    lists: list[str] | None = None
    tags: Tags | None = None
    document: Document | None = None


class CampaignSummary(BaseModel):
    id: str
    name: str
    subject: str
    preheader: str
    from_name: str
    from_email: str
    reply_to: str
    status: CampaignStatus
    lists: list[str]
    tags: list[str]
    audience: int
    """How many contacts it would reach right now: subscribed, not suppressed, each address once."""
    scheduled_at: str | None
    started_at: str | None
    sent_at: str | None
    last_error: str | None
    """Why sending stopped, when the worker paused a campaign by itself."""
    created_at: str
    updated_at: str


class Campaign(CampaignSummary):
    document: dict[str, Any]


class CampaignPage(BaseModel):
    data: list[CampaignSummary]
    next_cursor: str | None


class PreviewRequest(Strict):
    contact_id: str | None = Field(
        None, description="Fill merge tags with this contact. Defaults to sample data."
    )
    scheme: Literal["light", "dark"] = Field("light", description="Show the brand's dark palette")


class RenderRequest(Strict):
    document: Document
    subject: Text300 = ""
    preheader: Text300 = ""
    scheme: Literal["light", "dark"] = "light"


class TemplatePreview(Strict):
    template: Template
    colours: Colours = "template"
    scheme: Literal["light", "dark"] = "light"


class RenderedEmail(BaseModel):
    subject: str
    html: str
    text: str


class TestSend(Strict):
    to: Annotated[list[EmailStr], Field(min_length=1, max_length=5)]
    contact_id: str | None = Field(
        None, description="Fill merge tags with this contact. Defaults to sample data."
    )


class Schedule(Strict):
    at: AwareDatetime | None = Field(
        None, description="When to send, with a time zone offset. Omit to send now."
    )


class CampaignStats(BaseModel):
    status: CampaignStatus
    total: int
    queued: int
    """Waiting to be sent, including any being sent right now."""
    sent: int
    failed: int
    skipped: int
    delivered: int
    """Reported delivered by the provider."""
    bounced: int
    complained: int
    unsubscribed: int
