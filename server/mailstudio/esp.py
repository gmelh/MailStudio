"""Email service providers reached over HTTPS: Postmark, Mailgun and SMTP2GO.

Each turns a finished message into its provider's API call and reports SENT, RETRY or
FAILED like every provider (see `providers.py`). Problems that would hit every message,
such as a rejected API key or an unverified sender, raise `ProviderUnavailable` so the
campaign pauses instead of failing each email in turn.

Calls use only the standard library, so installs need no extra packages. A request that
times out after it was sent is reported FAILED, not retried: the provider may already
have accepted it, and a second copy is worse than a missing one (as with SES).
"""

from __future__ import annotations

import base64
import json
import secrets
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from .providers import Outcome, ProviderUnavailable, Result
from .repositories.settings import MailgunSettings, PostmarkSettings, Smtp2goSettings

TIMEOUT_SECONDS = 30
DELIVERY_UNKNOWN = "Timed out waiting for {name}; delivery unknown, so not retried to avoid a duplicate"


# ---------------------------------------------------------------------------
# HTTP


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: Any
    """Parsed JSON when the response is JSON, otherwise the text."""


class Unreachable(Exception):
    """The request never reached the provider (DNS, refused, connect timeout): safe to retry."""


class NoAnswer(Exception):
    """The request was sent but no answer came back: it may have been accepted."""


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"mailstudio-{secrets.token_hex(12)}"
    out = bytearray()
    for name, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += value.encode() + b"\r\n"
    for name, (filename, data, kind) in files.items():
        out += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {kind}\r\n\r\n"
        ).encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    form: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
    basic_auth: tuple[str, str] | None = None,
    timeout: float = TIMEOUT_SECONDS,
) -> Response:
    """One HTTPS request. Any status is returned; only transport problems raise."""
    sent_headers = {"Accept": "application/json", "User-Agent": "MailStudio", **(headers or {})}
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        sent_headers["Content-Type"] = "application/json"
    elif form is not None or files:
        data, sent_headers["Content-Type"] = _multipart(form or {}, files or {})
    if basic_auth:
        token = base64.b64encode(f"{basic_auth[0]}:{basic_auth[1]}".encode()).decode()
        sent_headers["Authorization"] = f"Basic {token}"
    req = urllib.request.Request(url, data=data, headers=sent_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as reply:
            status, raw = reply.status, reply.read(1024 * 1024)
    except urllib.error.HTTPError as exc:  # a real answer with an error status
        status, raw = exc.code, exc.read(1024 * 1024)
    except urllib.error.URLError as exc:  # raised before the request was fully sent
        raise Unreachable(str(exc.reason)) from exc
    except (TimeoutError, ConnectionError, OSError) as exc:
        raise NoAnswer(str(exc) or type(exc).__name__) from exc
    text = raw.decode("utf-8", errors="replace")
    try:
        return Response(status, json.loads(text))
    except ValueError:
        return Response(status, text)


Requester = Callable[..., Response]


def _detail(body: Any, *keys: str) -> str:
    if isinstance(body, dict):
        for key in keys:
            value = body.get(key)
            if value:
                return str(value)
        return json.dumps(body)[:300]
    return str(body)[:300]


def _classify(status: int, name: str, detail: str) -> Result:
    """Non-success statuses that aren't specific to one provider."""
    if status in (401, 403):
        raise ProviderUnavailable(
            f"{name} refused MailStudio's credentials. Check them in Settings. ({detail})"
        )
    if status in (408, 429) or status >= 500:
        return Result(Outcome.RETRY, error=f"{name} {status}: {detail}")
    return Result(Outcome.FAILED, error=f"{name} {status}: {detail}")


def _send(name: str, call: Callable[[], Response], read: Callable[[Response], Result]) -> Result:
    try:
        response = call()
    except Unreachable as exc:
        return Result(Outcome.RETRY, error=f"Couldn't reach {name}: {exc}")
    except NoAnswer:
        return Result(Outcome.FAILED, error=DELIVERY_UNKNOWN.format(name=name))
    return read(response)


def _bodies(message: EmailMessage) -> tuple[str, str]:
    text = message.get_body(("plain",))
    html = message.get_body(("html",))
    return (
        text.get_content() if isinstance(text, EmailMessage) else "",
        html.get_content() if isinstance(html, EmailMessage) else "",
    )


# ---------------------------------------------------------------------------
# Postmark

POSTMARK_API = "https://api.postmarkapp.com"
# Postmark error codes that mean no message will get through until something is fixed:
# bad token (10), sender signature missing or unconfirmed (400, 401), not allowed to send
# (405), account pending approval (412), unknown message stream (1235).
_POSTMARK_STOP = {10, 400, 401, 405, 412, 1235}


class PostmarkProvider:
    name = "Postmark"

    def __init__(self, settings: PostmarkSettings, http: Requester | None = None) -> None:
        if not settings.configured:
            raise ProviderUnavailable("Postmark isn't connected. Add the server token in Settings.")
        self.settings = settings
        self.http = http or request

    def payload(self, message: EmailMessage) -> dict[str, Any]:
        text, html = _bodies(message)
        headers = []
        if self.settings.own_unsubscribes:
            # Otherwise Postmark adds its own one-click headers; two sets would break them.
            headers = [
                {"Name": key, "Value": str(message[key])}
                for key in ("List-Unsubscribe", "List-Unsubscribe-Post")
                if message[key]
            ]
        body: dict[str, Any] = {
            "From": str(message["From"]),
            "To": str(message["To"]),
            "Subject": str(message["Subject"]),
            "TextBody": text,
            "HtmlBody": html,
            "MessageStream": self.settings.message_stream,
            "Headers": headers,
        }
        if message["Reply-To"]:
            body["ReplyTo"] = str(message["Reply-To"])
        return body

    def send(self, message: EmailMessage) -> Result:
        def read(response: Response) -> Result:
            body = response.body if isinstance(response.body, dict) else {}
            code = int(body.get("ErrorCode") or 0) if str(body.get("ErrorCode", "0")).isdigit() else -1
            detail = _detail(response.body, "Message")
            if 200 <= response.status < 300 and code == 0:
                return Result(Outcome.SENT, message_id=str(body.get("MessageID", "")) or None)
            if code in _POSTMARK_STOP:
                raise ProviderUnavailable(f"Postmark can't send yet: {detail} (error {code})")
            return _classify(response.status, self.name, f"{detail} (error {code})")

        return _send(
            self.name,
            lambda: self.http(
                "POST",
                f"{POSTMARK_API}/email",
                headers={"X-Postmark-Server-Token": self.settings.server_token},
                json_body=self.payload(message),
            ),
            read,
        )

    def close(self) -> None:
        pass


def check_postmark(settings: PostmarkSettings, http: Requester | None = None) -> str:
    http = http or request
    token = {"X-Postmark-Server-Token": settings.server_token}
    server = http("GET", f"{POSTMARK_API}/server", headers=token)
    if server.status != 200:
        raise CheckFailed(f"Postmark didn't accept the server token ({_detail(server.body, 'Message')}).")
    stream = http("GET", f"{POSTMARK_API}/message-streams/{settings.message_stream}", headers=token)
    if stream.status != 200:
        raise CheckFailed(
            f"Connected to the server, but it has no message stream “{settings.message_stream}”. "
            "Create a Broadcasts stream in Postmark and enter its ID."
        )
    kind = stream.body.get("MessageStreamType") if isinstance(stream.body, dict) else None
    name = server.body.get("Name", "your server") if isinstance(server.body, dict) else "your server"
    if kind and kind != "Broadcasts":
        raise CheckFailed(
            f"“{settings.message_stream}” is a {kind} stream. "
            "Newsletters must go through a Broadcasts stream."
        )
    return f"Connected to {name}, sending through the “{settings.message_stream}” stream."


# ---------------------------------------------------------------------------
# Mailgun


def mailgun_api(settings: MailgunSettings) -> str:
    return "https://api.eu.mailgun.net" if settings.region == "eu" else "https://api.mailgun.net"


class MailgunProvider:
    name = "Mailgun"

    def __init__(self, settings: MailgunSettings, http: Requester | None = None) -> None:
        if not settings.configured:
            raise ProviderUnavailable("Mailgun isn't connected. Add the API key and domain in Settings.")
        self.settings = settings
        self.http = http or request

    def send(self, message: EmailMessage) -> Result:
        def read(response: Response) -> Result:
            detail = _detail(response.body, "message")
            if 200 <= response.status < 300:
                raw_id = response.body.get("id", "") if isinstance(response.body, dict) else ""
                return Result(Outcome.SENT, message_id=str(raw_id).strip("<>") or None)
            if response.status == 404:
                raise ProviderUnavailable(
                    f"Mailgun doesn't know the domain {self.settings.domain}. ({detail})"
                )
            return _classify(response.status, self.name, detail)

        return _send(
            self.name,
            lambda: self.http(
                "POST",
                f"{mailgun_api(self.settings)}/v3/{self.settings.domain}/messages.mime",
                basic_auth=("api", self.settings.api_key),
                form={"to": str(message["To"])},
                files={"message": ("message.eml", message.as_bytes(), "message/rfc822")},
            ),
            read,
        )

    def close(self) -> None:
        pass


def check_mailgun(settings: MailgunSettings, http: Requester | None = None) -> str:
    http = http or request
    response = http(
        "GET",
        f"{mailgun_api(settings)}/v3/domains/{settings.domain}",
        basic_auth=("api", settings.api_key),
    )
    if response.status == 401:
        raise CheckFailed("Mailgun didn't accept the API key. (EU accounts: choose the EU region.)")
    if response.status == 404:
        raise CheckFailed(f"Mailgun has no domain called {settings.domain} on this account.")
    if response.status != 200:
        raise CheckFailed(f"Mailgun answered {response.status}: {_detail(response.body, 'message')}")
    domain = response.body.get("domain", {}) if isinstance(response.body, dict) else {}
    state = domain.get("state", "unknown")
    if state != "active":
        raise CheckFailed(
            f"Connected, but Mailgun says {settings.domain} is {state}. "
            "Add the DNS records Mailgun shows for it, "
            "then choose Verify in Mailgun."
        )
    return f"Connected. {settings.domain} is verified in Mailgun."


# ---------------------------------------------------------------------------
# SMTP2GO

SMTP2GO_API = "https://api.smtp2go.com/v3"
_SMTP2GO_STOP_HINTS = ("unauthorised sender", "unauthorized sender", "not verified", "sender domain")


class Smtp2goProvider:
    name = "SMTP2GO"

    def __init__(self, settings: Smtp2goSettings, http: Requester | None = None) -> None:
        if not settings.configured:
            raise ProviderUnavailable("SMTP2GO isn't connected. Add the API key in Settings.")
        self.settings = settings
        self.http = http or request

    def send(self, message: EmailMessage) -> Result:
        def read(response: Response) -> Result:
            body = response.body if isinstance(response.body, dict) else {}
            data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
            detail = _detail(data, "error", "failures") if data else _detail(response.body, "error")
            if 200 <= response.status < 300 and data.get("succeeded", 0) >= 1 and not data.get("failed"):
                return Result(Outcome.SENT, message_id=str(data.get("email_id", "")) or None)
            if any(hint in detail.lower() for hint in _SMTP2GO_STOP_HINTS):
                raise ProviderUnavailable(f"SMTP2GO won't send from this address yet: {detail}")
            if 200 <= response.status < 300:  # accepted the request, refused the recipient
                return Result(Outcome.FAILED, error=f"SMTP2GO: {detail}")
            return _classify(response.status, self.name, detail)

        return _send(
            self.name,
            lambda: self.http(
                "POST",
                f"{SMTP2GO_API}/email/mime",
                headers={"X-Smtp2go-Api-Key": self.settings.api_key},
                json_body={"mime_email": base64.b64encode(message.as_bytes()).decode()},
            ),
            read,
        )

    def close(self) -> None:
        pass


def check_smtp2go(settings: Smtp2goSettings, http: Requester | None = None) -> str:
    http = http or request
    response = http(
        "POST",
        f"{SMTP2GO_API}/stats/email_summary",
        headers={"X-Smtp2go-Api-Key": settings.api_key},
        json_body={},
    )
    if response.status in (401, 403):
        raise CheckFailed("SMTP2GO didn't accept the API key. Check it allows sending and reading stats.")
    if response.status != 200:
        raise CheckFailed(f"SMTP2GO answered {response.status}: {_detail(response.body, 'error')}")
    return "Connected to SMTP2GO."


class CheckFailed(Exception):
    """A connection check found a problem; the message says what to do."""
