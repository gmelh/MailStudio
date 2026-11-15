"""/api/v1/templates: designed emails to start a campaign from."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import gallery, render
from ..config import Settings as AppSettings
from ..deps import Db, ReadKey
from ..document import Document, check_sections
from ..errors import ProblemError, not_found
from ..repositories import campaigns
from ..schemas import RenderedEmail, TemplatePreview
from .campaigns import _business, _from_template, _library, _personalised

router = APIRouter(tags=["Templates"])


def _app(request: Request) -> AppSettings:
    settings: AppSettings = request.app.state.settings
    return settings


@router.get("/templates", summary="Templates that come with the app")
def list_templates(request: Request, _: ReadKey) -> dict[str, Any]:
    """Each template in full, so a picker can preview it. `gallery` says whether the online
    gallery is turned on."""
    app = _app(request)
    return {
        "templates": [t.summary() for t in gallery.bundled(app.sections_dir)],
        "categories": list(gallery.CATEGORIES),
        "gallery": bool(app.gallery_url),
    }


@router.get("/templates/gallery", summary="Templates in the online gallery")
def gallery_list(request: Request, _: ReadKey) -> dict[str, Any]:
    url = _app(request).gallery_url
    if not url:
        raise ProblemError(404, "gallery-off", "The online gallery is turned off")
    index = gallery.gallery_index(url)
    return {"source": url, "templates": gallery.gallery_previews(url, index.templates)}


@router.get("/templates/gallery/{template_id}", summary="One template from the online gallery")
def gallery_get(template_id: str, request: Request, _: ReadKey) -> dict[str, Any]:
    url = _app(request).gallery_url
    if not url:
        raise ProblemError(404, "gallery-off", "The online gallery is turned off")
    template = gallery.gallery_template(url, template_id)
    check_sections(template.document, _library(request))
    return template.summary()


@router.post("/templates/check", summary="Check a template file")
def check_template(body: dict[str, Any], request: Request, _: ReadKey) -> dict[str, Any]:
    """Validates a template (for example one someone downloaded) and returns it tidied up,
    or a 422 listing every problem. It also has to use only sections this install knows."""
    template = gallery.parse(body)
    check_sections(template.document, _library(request))
    return template.summary()


@router.post("/templates/preview", response_model=RenderedEmail, summary="Preview a template")
def preview_template(body: TemplatePreview, request: Request, conn: Db, _: ReadKey) -> RenderedEmail:
    """The template as it would start: with this install's logo and business details, and in
    the colours chosen."""
    library = _library(request)
    document = _from_template(conn, body.template, body.colours)
    check_sections(document, library)
    rendered = render.render(
        document,
        library,
        subject=body.template.subject,
        preheader=body.template.preheader,
        business=_business(conn),
        scheme=body.scheme,
        asset_base=_app(request).base_url,
    )
    return _personalised(conn, request, rendered, None)


@router.get("/campaigns/{campaign_id}/template", summary="Save a campaign as a template")
def export_template(campaign_id: str, conn: Db, _: ReadKey) -> JSONResponse:
    """A template file of the campaign's design and words, without the logo or business
    details, ready to share or to send to the gallery."""
    campaign = campaigns.get(conn, campaign_id)
    if campaign is None:
        raise not_found("Campaign", campaign_id)
    template = gallery.from_campaign(campaign, Document.model_validate(campaign["document"]))
    return JSONResponse(
        template.summary(),
        headers={"Content-Disposition": f'attachment; filename="{template.id}.mstemplate.json"'},
    )
