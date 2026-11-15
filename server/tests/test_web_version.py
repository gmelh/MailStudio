"""“View in browser”: each reader's own web copy, a shareable copy, and the link itself."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mailstudio.config import Settings
from mailstudio.worker import Worker
from tests.test_sending import (  # the sending harness
    Clock,
    FakeProvider,
    new_campaign,
    new_contact,
    new_list,
    ready,
    run_until_idle,
    schedule,
)

Headers = dict[str, str]
DOCUMENT = {
    "schema": 2,
    "sections": [
        {"id": "t", "type": "text", "settings": {"heading": "Hello {{first_name|friend}}", "body": "News."}}
    ],
}


@pytest.fixture
def send_key(make_key: Callable[[str], dict[str, str]]) -> dict[str, str]:
    return make_key("read write send")


@pytest.fixture
def make_worker(settings: Settings) -> Iterator[Callable[[Any], Worker]]:
    workers: list[Worker] = []

    def make(provider: Any) -> Worker:
        worker = Worker(settings, lambda _: provider, clock=Clock(), sleep=lambda _: None)
        workers.append(worker)
        return worker

    yield make
    for worker in workers:
        worker.conn.close()


def web_link(html: str) -> str:
    found = re.search(r'<a class="ms-link" href="([^"]+)"[^>]*>View in browser</a>', html)
    assert found, "no View in browser link"
    return found.group(1)


def sent(client: TestClient, send_key: Headers, make_worker: Any) -> tuple[str, str, FakeProvider]:
    ready(client, send_key)
    news = new_list(client, send_key)
    new_contact(client, send_key, "ann@example.com", [news], first_name="Ann")
    campaign = new_campaign(client, send_key, [news], document=DOCUMENT)
    assert schedule(client, send_key, campaign).status_code == 200
    provider = FakeProvider()
    run_until_idle(make_worker(provider))
    message = provider.messages[0]
    html = message.get_body(("html",)).get_content()
    return campaign, html, provider


def test_readers_get_their_own_web_copy(client: TestClient, send_key: Headers, make_worker: Any) -> None:
    _, html, provider = sent(client, send_key, make_worker)
    link = web_link(html)
    assert link.startswith("http://localhost:8080/v/")
    text = provider.messages[0].get_body(("plain",)).get_content()
    assert link in text  # the plain-text version has it too

    page = client.get(link.removeprefix("http://localhost:8080"))
    assert page.status_code == 200 and "Hello Ann" in page.text  # personalised, like the email
    assert page.headers["x-robots-tag"] == "noindex" and "no-store" in page.headers["cache-control"]
    unsubscribe = link.replace("/v/", "/u/")
    assert f'href="{unsubscribe}"' in page.text  # and it can still unsubscribe

    forged = link[:-3] + "AAA"
    assert client.get(forged.removeprefix("http://localhost:8080")).status_code == 404


def test_share_links_leave_out_personal_details(
    client: TestClient, send_key: Headers, make_worker: Any
) -> None:
    draft = new_campaign(client, send_key, [], document=DOCUMENT)
    assert client.get(f"/api/v1/campaigns/{draft}/share", headers=send_key).status_code == 409  # not sent yet

    campaign, _, _ = sent(client, send_key, make_worker)
    url = client.get(f"/api/v1/campaigns/{campaign}/share", headers=send_key).json()["url"]
    page = client.get(url.removeprefix("http://localhost:8080"))
    assert page.status_code == 200 and "Hello friend" in page.text and "Ann" not in page.text
    assert 'href="http://localhost:8080/u/shared"' in page.text and "noindex" not in page.text
    explained = client.get("/u/shared")
    assert "shared copy" in explained.text

    other = new_campaign(client, send_key, [], document=DOCUMENT)
    forged = url.replace(campaign, other)  # another campaign with this one's signature
    assert client.get(forged.removeprefix("http://localhost:8080")).status_code == 404


def test_the_link_can_be_turned_off(client: TestClient, auth: Headers) -> None:
    def render(brand: dict[str, Any]) -> str:
        body = {"document": {**DOCUMENT, "brand": brand}, "subject": "Hi"}
        return str(client.post("/api/v1/render", json=body, headers=auth).json()["html"])

    assert web_link(render({})) == "http://localhost:8080/v/preview"  # previews and tests point here
    assert "View in browser" not in render({"webLink": False})
    assert "Web version preview" in client.get("/v/preview").text
