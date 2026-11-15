"""/api/v1/campaigns and /api/v1/render"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from .. import gallery, images, mailer, render, tokens
from ..auth import ApiKey
from ..config import Settings as AppSettings
from ..deps import Db, IdempotencyKey, Paging, ReadKey, WriteKey, fingerprint, require, run_write
from ..document import Brand, Document, check_sections
from ..errors import ProblemError, not_found
from ..providers import Outcome, ProviderUnavailable, configured_provider
from ..repositories import campaigns, contacts, lists, settings
from ..repositories.settings import Business, Sender, Smtp
from ..schemas import (
    Campaign,
    CampaignCreate,
    CampaignPage,
    CampaignStats,
    CampaignStatus,
    CampaignUpdate,
    PreviewRequest,
    RenderedEmail,
    RenderRequest,
    Schedule,
    TestSend,
)
from ..sections import Library, load
from ..worker import build_snapshot

router = APIRouter(tags=["Campaigns"])
SendKey = Annotated[ApiKey, Depends(require("send"))]

SAMPLE_CONTACT = {"email": "subscriber@example.com", "first_name": "", "last_name": "", "fields": {}}


def _library(request: Request) -> Library:
    app: AppSettings = request.app.state.settings
    return load(app.sections_dir)


def _business(conn: Db) -> render.Business:
    b = settings.get_group(conn, "business", Business)
    return render.Business(name=b.name or "This email was sent by us", address=b.address)


def _starter(conn: Db) -> Document:
    """A first draft in the owner's brand, filled with their business details."""
    business = settings.get_group(conn, "business", Business)
    name = business.name or "Your Company"
    return Document.model_validate(
        {
            "schema": 2,
            "brand": settings.get_group(conn, "brand", Brand).model_dump(),
            "sections": [
                {"id": "header", "type": "header", "settings": {"brand": name.upper()}},
                {"id": "hero", "type": "hero", "settings": {}},
                {"id": "text", "type": "text", "settings": {}},
                {"id": "button", "type": "button", "settings": {}},
                {
                    "id": "footer",
                    "type": "footer",
                    "settings": {"company": name, "address": business.address or "Your business address"},
                },
            ],
        }
    )


def _from_template(conn: Db, template: gallery.Template, colours: gallery.Colours) -> Document:
    business = settings.get_group(conn, "business", Business)
    brand = settings.get_group(conn, "brand", Brand)
    return gallery.apply(template, brand, business.name, business.address, colours)


def _check_targets(conn: Db, list_ids: list[str] | None) -> None:
    if list_ids:
        missing = sorted(set(list_ids) - lists.existing_ids(conn, list_ids))
        if missing:
            raise ProblemError(
                422, "unknown-lists", "Unknown lists", "Some list IDs don't exist.", list_ids=missing
            )


def _editable(conn: Db, campaign_id: str) -> dict[str, Any]:
    campaign = campaigns.get(conn, campaign_id)
    if campaign is None:
        raise not_found("Campaign", campaign_id)
    if campaign["status"] not in campaigns.EDITABLE:
        raise ProblemError(
            409,
            "campaign-locked",
            "Campaign can't be changed",
            f"This campaign is {campaign['status']}. Only drafts can be edited or deleted.",
        )
    return campaign


def _render_campaign(
    conn: Db, request: Request, campaign: dict[str, Any], scheme: str = "light"
) -> render.Rendered:
    document = Document.model_validate(campaign["document"])
    library = _library(request)
    check_sections(document, library)
    return render.render(
        document,
        library,
        subject=campaign["subject"],
        preheader=campaign["preheader"],
        business=_business(conn),
        scheme=scheme,
        asset_base=request.app.state.settings.base_url,
    )


def image_report(conn: Db, request: Request, campaign: dict[str, Any]) -> images.Report:
    """What would stop the campaign's images showing, and what would make them better."""
    rendered = _render_campaign(conn, request, campaign)
    library = _library(request)
    document = Document.model_validate(campaign["document"])
    sections = [
        (
            section.id,
            library.sections[section.type].label if section.type in library.sections else section.type,
        )
        for section in document.sections
    ]
    return images.check(conn, rendered.html, rendered.text, sections, request.app.state.settings.base_url)


def _personalised(
    conn: Db, request: Request, rendered: render.Rendered, contact_id: str | None
) -> RenderedEmail:
    contact: dict[str, Any] = SAMPLE_CONTACT
    if contact_id:
        found = contacts.get(conn, contact_id)
        if found is None:
            raise not_found("Contact", contact_id)
        contact = found
    app: AppSettings = request.app.state.settings
    values = render.merge_values(contact, f"{app.base_url}/u/preview", f"{app.base_url}/v/preview")
    return RenderedEmail(
        subject=render.personalise(rendered.subject, values, escape=False),
        html=render.personalise(rendered.html, values, escape=True),
        text=render.personalise(rendered.text, values, escape=False),
    )


# ---------------------------------------------------------------------------
# CRUD


@router.get("/campaigns", response_model=CampaignPage, summary="List campaigns")
def list_campaigns(
    conn: Db, _: ReadKey, page: Paging, status: Annotated[CampaignStatus | None, Query()] = None
) -> dict[str, Any]:
    return page.result(campaigns.page(conn, after=page.after, limit=page.limit, status=status))


@router.post("/campaigns", response_model=Campaign, status_code=201, summary="Create a draft")
def create_campaign(
    body: CampaignCreate, request: Request, conn: Db, key: WriteKey, idempotency_key: IdempotencyKey = None
) -> JSONResponse:
    def action() -> tuple[int, Any]:
        _check_targets(conn, body.lists)
        template = body.template
        if body.template_id and not template:
            template = gallery.find_bundled(request.app.state.settings.sections_dir, body.template_id)
            if template is None:
                raise not_found("Template", body.template_id)
        document = body.document or (
            _from_template(conn, template, body.colours) if template else _starter(conn)
        )
        check_sections(document, _library(request))
        sender = settings.get_group(conn, "sender", Sender)
        values = body.model_dump(exclude={"document", "template", "template_id", "colours"})
        if template and body.document is None:
            values["subject"] = values["subject"] or template.subject
            values["preheader"] = values["preheader"] or template.preheader
        values["from_name"] = values["from_name"] if values["from_name"] is not None else sender.from_name
        values["from_email"] = values["from_email"] or sender.from_email or ""
        values["reply_to"] = values["reply_to"] or sender.reply_to or ""
        campaign_id = campaigns.create(conn, values, document.dump())
        return 201, campaigns.get(conn, campaign_id)

    return run_write(conn, key, idempotency_key, fingerprint(request, body), action)


@router.post(
    "/campaigns/{campaign_id}/duplicate", response_model=Campaign, status_code=201, summary="Copy a campaign"
)
def duplicate_campaign(campaign_id: str, request: Request, conn: Db, key: WriteKey) -> JSONResponse:
    """A new draft with the same design, subject, sender and audience. Works for any
    campaign, so a sent newsletter can be the starting point for the next one."""

    def action() -> tuple[int, Any]:
        source = _campaign_or_404(conn, campaign_id)
        values = {
            k: source[k] for k in ("subject", "preheader", "from_name", "from_email", "reply_to", "tags")
        }
        values["name"] = f"Copy of {source['name']}"[:200]
        values["lists"] = sorted(lists.existing_ids(conn, source["lists"]))
        new_id = campaigns.create(conn, values, source["document"])
        return 201, campaigns.get(conn, new_id)

    return run_write(conn, key, None, fingerprint(request, None), action)


@router.get("/campaigns/{campaign_id}", response_model=Campaign, summary="Get a campaign")
def get_campaign(campaign_id: str, conn: Db, _: ReadKey) -> dict[str, Any]:
    campaign = campaigns.get(conn, campaign_id)
    if campaign is None:
        raise not_found("Campaign", campaign_id)
    return campaign


@router.patch("/campaigns/{campaign_id}", response_model=Campaign, summary="Update a draft")
def update_campaign(
    campaign_id: str, body: CampaignUpdate, request: Request, conn: Db, key: WriteKey
) -> JSONResponse:
    values = body.model_dump(exclude={"document"}, exclude_unset=True)
    if values.get("name", "") is None:
        del values["name"]

    def action() -> tuple[int, Any]:
        _editable(conn, campaign_id)
        _check_targets(conn, values.get("lists"))
        if body.document is not None:
            check_sections(body.document, _library(request))
        campaigns.update(conn, campaign_id, values, body.document.dump() if body.document else None)
        return 200, campaigns.get(conn, campaign_id)

    return run_write(conn, key, None, fingerprint(request, body), action)


@router.delete("/campaigns/{campaign_id}", status_code=204, summary="Delete a draft")
def delete_campaign(campaign_id: str, request: Request, conn: Db, key: WriteKey) -> Response:
    def action() -> tuple[int, Any]:
        _editable(conn, campaign_id)
        campaigns.delete(conn, campaign_id)
        return 204, None

    run_write(conn, key, None, fingerprint(request, None), action)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Rendering and test sends


@router.post("/campaigns/{campaign_id}/preview", response_model=RenderedEmail, summary="Preview a campaign")
def preview_campaign(
    campaign_id: str, request: Request, conn: Db, _: ReadKey, body: PreviewRequest | None = None
) -> RenderedEmail:
    """The email exactly as a recipient would get it, with merge tags filled from
    `contact_id` or from sample data. The unsubscribe link points to a preview page."""
    campaign = campaigns.get(conn, campaign_id)
    if campaign is None:
        raise not_found("Campaign", campaign_id)
    rendered = _render_campaign(conn, request, campaign, body.scheme if body else "light")
    return _personalised(conn, request, rendered, body.contact_id if body else None)


@router.post("/render", response_model=RenderedEmail, summary="Render a document without saving it")
def render_document(body: RenderRequest, request: Request, conn: Db, _: ReadKey) -> RenderedEmail:
    """For CI checks and tools: render any campaign document with sample data."""
    library = _library(request)
    check_sections(body.document, library)
    rendered = render.render(
        body.document,
        library,
        subject=body.subject,
        preheader=body.preheader,
        business=_business(conn),
        scheme=body.scheme,
        asset_base=request.app.state.settings.base_url,
    )
    return _personalised(conn, request, rendered, None)


@router.get("/sections", summary="Section types, their fields, and style presets")
def section_library(request: Request, _: ReadKey) -> dict[str, Any]:
    """What the editor needs to offer sections and their settings."""
    library = _library(request)
    return {"sections": library.manifest["sections"], "presets": library.presets}


@router.post("/campaigns/{campaign_id}/test", status_code=202, summary="Send a test email")
def test_send(campaign_id: str, body: TestSend, request: Request, conn: Db, _: SendKey) -> dict[str, Any]:
    """Sends the campaign to up to five addresses, with "[Test]" before the subject:
    through the test-email SMTP server in Settings if there is one, otherwise through the
    sending service.
    Subscribers are never emailed."""
    campaign = campaigns.get(conn, campaign_id)
    if campaign is None:
        raise not_found("Campaign", campaign_id)
    if not campaign["from_email"]:
        raise ProblemError(
            422, "no-sender", "No sender address", "Set `from_email` on the campaign or in Settings."
        )
    email = _personalised(conn, request, _render_campaign(conn, request, campaign), body.contact_id)
    app: AppSettings = request.app.state.settings
    messages = [
        mailer.build(
            mailer.Message(
                to=str(address),
                from_name=campaign["from_name"],
                from_email=campaign["from_email"],
                reply_to=campaign["reply_to"],
                subject=f"[Test] {email.subject}",
                html=email.html,
                text=email.text,
                unsubscribe_url=f"{app.base_url}/u/preview",
            )
        )
        for address in body.to
    ]
    smtp = settings.get_group(conn, "smtp", Smtp)
    if smtp.host or not settings.active_provider(conn):
        try:
            mailer.send_smtp(smtp, messages)
        except mailer.SmtpError as exc:
            raise ProblemError(502, "smtp-failed", "Test send failed", str(exc)) from None
    else:
        try:
            provider = configured_provider(conn)
        except ProviderUnavailable as exc:
            raise ProblemError(502, "send-failed", "Test send failed", str(exc)) from None
        try:
            for message in messages:
                result = provider.send(message)
                if result.outcome is not Outcome.SENT:
                    raise ProblemError(
                        502,
                        "send-failed",
                        "Test send failed",
                        result.error or "The sending service refused it.",
                    )
        except ProviderUnavailable as exc:
            raise ProblemError(502, "send-failed", "Test send failed", str(exc)) from None
        finally:
            provider.close()
    return {"sent_to": [str(a) for a in body.to]}


# ---------------------------------------------------------------------------
# Sending


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _campaign_or_404(conn: Db, campaign_id: str) -> dict[str, Any]:
    campaign = campaigns.get(conn, campaign_id)
    if campaign is None:
        raise not_found("Campaign", campaign_id)
    return campaign


def _wrong_state(campaign: dict[str, Any], action: str) -> ProblemError:
    return ProblemError(
        409, "campaign-state", f"Can't {action} this campaign", f"The campaign is {campaign['status']}."
    )


def _set_status(conn: Db, campaign_id: str, status: str, **columns: Any) -> None:
    values = {"status": status, **columns, "updated_at": _iso(datetime.now(UTC))}
    assignments = ", ".join(f"{k} = ?" for k in values)  # keys are fixed by the callers below
    conn.execute(f"UPDATE campaigns SET {assignments} WHERE id = ?", (*values.values(), campaign_id))


@router.get("/campaigns/{campaign_id}/share", summary="A link to share a sent campaign")
def campaign_share(campaign_id: str, request: Request, conn: Db, _: ReadKey) -> dict[str, str]:
    """A web copy anyone can see, for social media: personal details are left out, and
    its unsubscribe link explains where to unsubscribe. Only once the campaign is sending."""
    campaign = campaigns.get(conn, campaign_id)
    if campaign is None:
        raise not_found("Campaign", campaign_id)
    row = conn.execute("SELECT snapshot FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not row or not row["snapshot"]:
        raise ProblemError(409, "not-sent", "Not sent yet", "A campaign can be shared once it's sending.")
    base = request.app.state.settings.base_url
    return {"url": f"{base}/v/s/{tokens.make_share(settings.secret_key(conn), campaign_id)}"}


@router.get("/campaigns/{campaign_id}/checks", summary="Check the campaign's images before sending")
def campaign_checks(campaign_id: str, request: Request, conn: Db, _: ReadKey) -> dict[str, list[str]]:
    """`problems` stop it being sent (an image recipients can't load); `warnings` are
    worth fixing (missing descriptions, blurry or heavy images, image-only emails)."""
    campaign = campaigns.get(conn, campaign_id)
    if campaign is None:
        raise not_found("Campaign", campaign_id)
    return image_report(conn, request, campaign).as_dict()


@router.post("/campaigns/{campaign_id}/schedule", response_model=Campaign, summary="Schedule or send now")
def schedule_campaign(
    campaign_id: str,
    request: Request,
    conn: Db,
    key: SendKey,
    body: Schedule | None = None,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Checks the campaign is ready, then queues it for `at` (or now). From this point
    the campaign can't be edited; cancel a scheduled campaign to return it to draft."""

    def action() -> tuple[int, Any]:
        campaign = _campaign_or_404(conn, campaign_id)
        if campaign["status"] != "draft":
            raise _wrong_state(campaign, "schedule")
        business = settings.get_group(conn, "business", Business)
        problems = [
            message
            for ok, message in (
                (
                    bool(business.name and business.address),
                    "Add your business name and postal address in Settings "
                    "(the law requires them in every email).",
                ),
                (bool(campaign["subject"].strip()), "Add a subject line."),
                (bool(campaign["from_email"]), "Set a sender address (`from_email`)."),
                (bool(campaign["lists"]), "Choose at least one list to send to."),
                (
                    campaign["audience"] > 0 or not campaign["lists"],
                    "Nobody on those lists can be emailed right now.",
                ),
            )
            if not ok
        ]
        problems += image_report(conn, request, campaign).problems
        if problems:
            raise ProblemError(
                422, "not-ready", "Campaign isn't ready to send", " ".join(problems), problems=problems
            )
        build_snapshot(conn, request.app.state.settings, campaign)  # surfaces render problems now, not later

        moment = datetime.now(UTC)
        if body and body.at:
            if body.at > moment + timedelta(days=366):
                raise ProblemError(422, "too-far-ahead", "Too far ahead", "Schedule at most a year ahead.")
            moment = max(moment, body.at)
        _set_status(conn, campaign_id, "scheduled", scheduled_at=_iso(moment), last_error=None)
        return 200, campaigns.get(conn, campaign_id)

    return run_write(conn, key, idempotency_key, fingerprint(request, body), action)


@router.post("/campaigns/{campaign_id}/pause", response_model=Campaign, summary="Pause sending")
def pause_campaign(campaign_id: str, request: Request, conn: Db, key: SendKey) -> JSONResponse:
    def action() -> tuple[int, Any]:
        campaign = _campaign_or_404(conn, campaign_id)
        if campaign["status"] != "sending":
            raise _wrong_state(campaign, "pause")
        _set_status(conn, campaign_id, "paused")
        return 200, campaigns.get(conn, campaign_id)

    return run_write(conn, key, None, fingerprint(request, None), action)


@router.post("/campaigns/{campaign_id}/resume", response_model=Campaign, summary="Resume sending")
def resume_campaign(campaign_id: str, request: Request, conn: Db, key: SendKey) -> JSONResponse:
    def action() -> tuple[int, Any]:
        campaign = _campaign_or_404(conn, campaign_id)
        if campaign["status"] != "paused":
            raise _wrong_state(campaign, "resume")
        if campaign["started_at"]:
            _set_status(conn, campaign_id, "sending", last_error=None)
        else:  # paused before it could start (e.g. it failed to render): try starting again
            _set_status(conn, campaign_id, "scheduled", scheduled_at=_iso(datetime.now(UTC)), last_error=None)
        return 200, campaigns.get(conn, campaign_id)

    return run_write(conn, key, None, fingerprint(request, None), action)


@router.post("/campaigns/{campaign_id}/cancel", response_model=Campaign, summary="Cancel")
def cancel_campaign(campaign_id: str, request: Request, conn: Db, key: SendKey) -> JSONResponse:
    """A scheduled campaign goes back to draft. A campaign that has started sending
    stops: emails already sent stay sent, and the rest are skipped."""

    def action() -> tuple[int, Any]:
        campaign = _campaign_or_404(conn, campaign_id)
        if campaign["status"] == "scheduled":
            _set_status(conn, campaign_id, "draft", scheduled_at=None)
        elif campaign["status"] in ("sending", "paused") and campaign["started_at"]:
            conn.execute(
                "UPDATE send_jobs SET status = 'skipped', error = 'Campaign cancelled'"
                " WHERE campaign_id = ? AND status = 'queued'",
                (campaign_id,),
            )
            _set_status(conn, campaign_id, "cancelled")
        elif campaign["status"] == "paused":
            _set_status(conn, campaign_id, "draft", scheduled_at=None)
        else:
            raise _wrong_state(campaign, "cancel")
        return 200, campaigns.get(conn, campaign_id)

    return run_write(conn, key, None, fingerprint(request, None), action)


@router.get("/campaigns/{campaign_id}/stats", response_model=CampaignStats, summary="Delivery counts")
def campaign_stats(campaign_id: str, conn: Db, _: ReadKey) -> dict[str, Any]:
    campaign = _campaign_or_404(conn, campaign_id)
    counts = dict.fromkeys(("queued", "sent", "failed", "skipped"), 0)
    for row in conn.execute(
        "SELECT status, COUNT(*) AS n FROM send_jobs WHERE campaign_id = ? GROUP BY status", (campaign_id,)
    ):
        bucket = "queued" if row["status"] in ("claimed", "sending") else row["status"]
        counts[bucket] += row["n"]
    events = dict.fromkeys(("delivery", "bounce", "complaint"), 0)
    for row in conn.execute(
        "SELECT type, COUNT(DISTINCT email) AS n FROM events WHERE campaign_id = ? GROUP BY type",
        (campaign_id,),
    ):
        if row["type"] in events:
            events[row["type"]] = row["n"]
    # Unsubscribes record the campaign in their source: "<how> (<campaign id>)".
    unsubscribed = conn.execute(
        "SELECT COUNT(DISTINCT contact_id) FROM consent_events"
        " WHERE action = 'unsubscribe' AND source LIKE ?",
        (f"%({campaign_id})",),
    ).fetchone()[0]
    return {
        "status": campaign["status"],
        "total": sum(counts.values()),
        "unsubscribed": unsubscribed,
        **counts,
        "delivered": events["delivery"],
        "bounced": events["bounce"],
        "complained": events["complaint"],
    }
