"""URL canonicalization and domain policy helpers (pure functions)."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = (
    "utm_",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "igshid",
    "yclid",
)
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?$")

PRIMARY_DOMAIN_HINTS = (
    "sec.gov",
    ".gov",
    "europa.eu",
    "ecb.europa.eu",
    "federalreserve.gov",
    "consumerfinance.gov",
    "ftc.gov",
    "companieshouse.gov.uk",
    "gov.uk",
    "oecd.org",
    "worldbank.org",
    "imf.org",
    "bis.org",
    "ons.gov.uk",
    "census.gov",
    "bls.gov",
    "acea.auto",
    "nada.org",
    "investor.",
    "ir.",
    "annualreport",
    "10-k",
    "10k",
)


def canonical_url(url: str) -> str:
    """Lowercase host, drop fragments/tracking params, strip trailing slash."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


def domain_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def looks_like_domain(entry: str) -> bool:
    return bool(_DOMAIN_RE.match(entry.strip().lower()))


def domain_matches(domain: str, entry: str) -> bool:
    entry = entry.strip().lower().split("/")[0]
    return domain == entry or domain.endswith("." + entry)


def is_banned(domain: str, banned: list[str]) -> bool:
    return any(domain_matches(domain, b) for b in banned if looks_like_domain(b))


def is_allowed_listed(domain: str, allowed: list[str]) -> bool:
    return any(domain_matches(domain, a) for a in allowed if looks_like_domain(a))


def looks_primary(url: str, domain: str) -> bool:
    haystack = (url + " " + domain).lower()
    return any(hint in haystack for hint in PRIMARY_DOMAIN_HINTS)
