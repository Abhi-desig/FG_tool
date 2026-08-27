"""What actually holds the line on a localhost server.

SECURITY.md §1 treated the `127.0.0.1` bind as the whole answer. It is not. The
bind stops anyone on the shop's Wi-Fi reaching the port; it does nothing about a
page already open in the operator's own browser.

**Why that matters here.** A multipart `POST` is a CORS *simple* request: any
page on any site can fire one at `127.0.0.1:8000` without a preflight and
without the browser asking. It cannot read the reply — the same-origin policy
still holds — but it does not need to. The spend on `/api/ai/photo-edit` and the
CPU burn on `/api/images/upscale` happen anyway, and on a 12 GB shop PC a queue
of upscales is the whole machine. DNS rebinding is open for the same reason: a
hostile domain that resolves to `127.0.0.1` is same-origin as far as the browser
is concerned, and only the `Host` header gives it away.

**Two checks, both cheap:**

* **Host** must be a loopback name. This is what closes DNS rebinding — a
  rebound request arrives with the attacker's hostname in `Host`, not ours.
* **Origin / Sec-Fetch-Site** must be same-origin on anything that changes
  state. Browsers have sent `Sec-Fetch-Site` for years and it cannot be forged
  from script, which makes it a better signal than a CSRF token nobody would
  rotate. A request with *no* `Origin` and no `Sec-Fetch-Site` is allowed: that
  is `curl`, the shipped diagnostic script, and the test client — all local, all
  deliberate, and none of them a browser being used as a weapon.

No token is minted. A token would have to live somewhere the page can read,
which on a single-origin app adds a moving part without adding a barrier the
headers above do not already provide.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# Names that mean "this machine". `Host` carries no scheme, and may carry a port.
LOOPBACK_HOSTS = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "[::1]",
        "::1",
    }
)

# Methods that change something, spend something, or cost CPU.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# `Sec-Fetch-Site` values that are not the app talking to itself.
CROSS_SITE = frozenset({"cross-site", "same-site"})


def _hostname(header: str) -> str:
    """Strip the port from a Host header, leaving an IPv6 literal intact."""
    value = header.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1] if end != -1 else value
    return value.rsplit(":", 1)[0] if ":" in value else value


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    """Refuse requests that are not this machine talking to itself."""

    def __init__(self, app: ASGIApp, port: int) -> None:
        super().__init__(app)
        self._expected_origins = {
            f"http://{host}:{port}"
            for host in ("127.0.0.1", "localhost", "[::1]")
        }

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        host = request.headers.get("host", "")
        if host and _hostname(host) not in LOOPBACK_HOSTS:
            # A rebound DNS name, or a proxy someone has put in front of this.
            return JSONResponse(
                status_code=421,
                content={
                    "detail": (
                        f"This server only answers to localhost. It was asked for "
                        f"“{_hostname(host)}”. Open it at http://127.0.0.1 — and if "
                        f"you did not, something else on this machine is trying to "
                        f"reach it."
                    )
                },
            )

        if request.method in UNSAFE_METHODS:
            refusal = self._check_origin(request)
            if refusal is not None:
                return refusal

        return await call_next(request)

    def _check_origin(self, request: Request) -> JSONResponse | None:
        """Same-origin, or no browser involved at all. Nothing else."""
        site = request.headers.get("sec-fetch-site", "").lower()
        if site in CROSS_SITE:
            return self._refuse()
        if site in {"same-origin", "none"}:
            return None

        origin = request.headers.get("origin")
        if origin and origin.lower() not in self._expected_origins:
            return self._refuse()

        # No Origin and no Sec-Fetch-Site: not a browser. See the module
        # docstring — this is curl, check-ai.command, and the test client.
        return None

    @staticmethod
    def _refuse() -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "That request came from another website. Focus Toolkit only "
                    "accepts requests from its own pages — otherwise any site you "
                    "have open could spend your Google credit or start a job on "
                    "this machine."
                )
            },
        )


__all__ = ["LOOPBACK_HOSTS", "LocalOnlyMiddleware", "UNSAFE_METHODS"]
