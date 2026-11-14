"""DNS checks for the sending domain, written for owners who've never edited DNS.

Each record says exactly what to add, where, and whether it's there yet:

- DKIM: three CNAME records from Amazon SES (Easy DKIM). Required.
- DMARC: one TXT record at _dmarc.<domain>. Gmail and Yahoo require one for bulk senders.
- SPF: not needed for SES's default setup, but more than one SPF record on the
  domain breaks SPF for everything else it sends, so that's flagged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

import dns.exception
import dns.resolver

from .ses import Identity

Status = Literal["ok", "missing", "wrong", "warning"]
LOOKUP_TIMEOUT = 5.0


def txt_records(name: str) -> list[str]:
    try:
        answer = dns.resolver.resolve(name, "TXT", lifetime=LOOKUP_TIMEOUT)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return []
    return ["".join(part.decode(errors="replace") for part in record.strings) for record in answer]


def cname_target(name: str) -> str | None:
    try:
        answer = dns.resolver.resolve(name, "CNAME", lifetime=LOOKUP_TIMEOUT)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return None
    return str(answer[0].target).rstrip(".").lower()


_DMARC_HELP = (
    "Start with p=none, which only monitors. Tighten to p=quarantine once everything you send passes."
)


@dataclass(frozen=True, slots=True)
class Record:
    purpose: str
    type: Literal["CNAME", "TXT"]
    host: str
    value: str
    """What to enter (for TXT records, a sensible starting value)."""
    status: Status
    found: list[str]
    help: str


def check(domain: str, identity: Identity | None) -> list[Record]:
    records: list[Record] = []

    for token in identity.dkim_tokens if identity else []:
        host = f"{token}._domainkey.{domain}"
        expected = f"{token}.dkim.amazonses.com"
        found = cname_target(host)
        records.append(
            Record(
                purpose="DKIM (signs your emails)",
                type="CNAME",
                host=host,
                value=expected,
                status="ok" if found == expected else ("wrong" if found else "missing"),
                found=[found] if found else [],
                help="Add this CNAME record at your domain's DNS provider. Some providers want only the part "
                f"before .{domain} in the host field.",
            )
        )

    dmarc_host = f"_dmarc.{domain}"
    dmarc = [r for r in txt_records(dmarc_host) if r.lower().startswith("v=dmarc1")]
    records.append(
        Record(
            purpose="DMARC (required by Gmail and Yahoo)",
            type="TXT",
            host=dmarc_host,
            value=f"v=DMARC1; p=none; rua=mailto:dmarc@{domain}",
            status="ok" if len(dmarc) == 1 else ("wrong" if dmarc else "missing"),
            found=dmarc,
            help=_DMARC_HELP
            if not dmarc
            else ("Only one DMARC record is allowed; remove the extras." if len(dmarc) > 1 else ""),
        )
    )

    spf = [r for r in txt_records(domain) if r.lower().startswith("v=spf1")]
    records.append(
        Record(
            purpose="SPF (optional with Amazon SES)",
            type="TXT",
            host=domain,
            value="",
            status="warning" if len(spf) > 1 else "ok",
            found=spf,
            help="Your domain has more than one SPF record, which makes SPF fail for all its email. "
            "Merge them into one."
            if len(spf) > 1
            else "Nothing to do for Mail Studio.",
        )
    )
    return records


def summary(domain: str, identity: Identity | None, records: list[Record]) -> dict[str, Any]:
    required_ok = all(r.status == "ok" for r in records if not r.purpose.startswith("SPF"))
    return {
        "domain": domain,
        "registered_with_ses": identity is not None,
        "ses_verified": bool(identity and identity.verified),
        "dkim_status": identity.dkim_status if identity else "NOT_STARTED",
        "ready": bool(identity and identity.verified and identity.dkim_status == "SUCCESS" and required_ok),
        "records": [asdict(r) for r in records],
    }
