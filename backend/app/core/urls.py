"""URL hygiene for venue requests.

A broker's host comes from three places — the built-in ``DEFAULTS``, an
admin-editable ``broker_definitions`` row, and (during a connection check) the
environment that accepted the key — and all three end up in the same
f-string::

    url = f"{base_url}{path}"

That is the whole request-building surface of this app, and every bug of this
shape reports identically from the outside::

    ConnectionError: Failed to resolve 'api.india.delta.exchangeget '

…because something that is not a hostname was concatenated onto it: a pasted
trailing space, a display label (``"GET /v2/wallet/balances"``), or a method
token that belongs only in the HMAC signature string. The venue never sees the
request, so the error blames the network while the bug is in this repo — which
is expensive to read and cheap to prevent. Two rules, applied in one place:

* :func:`normalize_base_url` — what is stored, trimmed of the differences that
  are not differences (whitespace, trailing ``/``);
* :func:`url_problem` / :func:`path_problem` — what must never be *sent*,
  checked before the socket opens, so no weight is spent and the message names
  the mistake instead of a resolver.

No venue credentials and no signing live here on purpose: Binance and Delta
build their ``signature_data`` strings differently, but both must be unable to
aim a request at a host that is not a host.
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Dict, Optional

#: Words that belong in a signature/request line and never inside a path.
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})

#: Prefix of every error this module's callers raise for a request the client
#: refused to build. ``delta_key_probe`` keys off it, so the phrasing here is
#: part of the diagnostic contract: changing it changes how a report reads.
REFUSAL_PREFIX = "request not sent"


def normalize_base_url(value: Any, fallback: Any = "") -> str:
    """Whitespace-free, trailing-slash-free base URL (``fallback`` if empty).

    Trimming is lossless: ``"https://api.india.delta.exchange/ "`` and
    ``"https://api.india.delta.exchange"`` are the same host, and the space
    between them is what turns a signed call into a DNS lookup for
    ``api.india.delta.exchangeget%20``. Anything *else* wrong with the string
    is left untouched so :func:`url_problem` can name it — silently rewriting
    a bad host into a good one would trade a loud error for a wrong venue.
    """
    def _clean(candidate: Any) -> str:
        text = str(candidate if candidate is not None else "").strip()
        return text.rstrip("/").strip()

    cleaned = _clean(value)
    return cleaned or _clean(fallback)


def _scheme_and_netloc(text: str):
    """``(scheme, netloc)`` for a URL, or ``(None, None)`` when unparseable."""
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return None, None
    return parts.scheme, parts.netloc


def url_problem(url: Any) -> Optional[str]:
    """Why ``url`` cannot be requested, or ``None`` when it can be sent."""
    text = str(url or "")
    scheme, netloc = _scheme_and_netloc(text)
    if scheme is None:
        return f"the request URL {text!r} cannot be parsed"
    if scheme not in ("http", "https"):
        return f"the request URL {text!r} has no http(s) scheme"
    if not netloc:
        return f"the request URL {text!r} has no host"
    # The authority ends at the first space, so a stray token glued onto the
    # base URL silently becomes part of the hostname: percent-encoded by the
    # time it reaches a resolver and unreadable in the failure.
    if any(c.isspace() for c in netloc) or "%" in netloc:
        return (f"the host of {text!r} is {netloc!r}, which is not a hostname — a "
                f"value was appended to the base URL, so the request would go to a "
                f"host nobody configured")
    if "@" in netloc:
        return f"the request URL {text!r} must not embed credentials"
    return None


def path_problem(path: Any) -> Optional[str]:
    """Why ``path`` cannot be a request path, or ``None`` when it can.

    Both venues sign over the path (Delta: ``METHOD + timestamp + path +
    query_string + body``; Binance: the serialized query), so a path carrying
    a method token or its own query string is *also* a signature mismatch —
    the call could never have been accepted, whatever the transport does with
    it. Rejecting it here keeps that fact from being reported as ``401``.
    """
    # Same trimming rule as normalize_base_url: surrounding whitespace is a
    # paste artifact, everything inside the string is a decision.
    text = str(path or "").strip()
    if not text:
        return "the request path is empty"
    if not text.startswith("/"):
        first = text.strip().split()[0].rstrip(":;,")
        if first.upper() in HTTP_METHODS:
            return (f"the request path {text!r} starts with an HTTP method — that is a "
                    f"display label, not an endpoint. Send the method separately "
                    f"(method={first.upper()!r}) and pass the bare path, with any query "
                    f"in query={{...}} rather than glued onto it")
        return (f"the request path {text!r} must start with '/' — the host comes from "
                f"the broker's base URL, never from the path")
    if "?" in text or "#" in text:
        return (f"the request path {text!r} contains a query string — pass it as "
                f"query={{...}} so it is signed and encoded exactly once")
    if any(c.isspace() for c in text):
        return f"the request path {text!r} contains whitespace"
    return None


def refusal(venue: str, problem: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The error dict a guarded request answers with — never raised, never sent.

    ``context`` is echoed back so a caller building a report (the connection
    battery) can show *what it asked for* next to the venue's answer, which is
    the only way an operator can tell "the exchange refused me" from "this code
    built a nonsense URL".
    """
    out: Dict[str, Any] = {"error": f"{venue} {REFUSAL_PREFIX}: {problem}"}
    if context:
        out.update(context)
    return out
