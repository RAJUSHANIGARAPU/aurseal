"""Server-rendered, read-only web UI (Jinja2), served by the same app as the API.

The page logic is framework-free (:mod:`.pages`, :mod:`.views`); this module only renders
templates and mounts the pages on the web app. Jinja2 is imported inside functions
(``requirements-platform.txt``). No ``from __future__ import annotations``: FastAPI
reads the endpoint's ``Request`` annotation at runtime.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

TEMPLATES = Path(__file__).resolve().parent / "templates"


@lru_cache(maxsize=1)
def environment() -> Any:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

    return Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
    )


def render(template: str, context: dict[str, Any]) -> str:
    return environment().get_template(template).render(**context)


def mount(app: Any, services: Any) -> None:
    """Add the UI pages (``/ui/...``) to a FastAPI app."""
    from fastapi import Request
    from fastapi.responses import HTMLResponse, RedirectResponse

    from qaplatform.http.ui.pages import COOKIE, UI, UI_ROUTES, ui_handle

    async def endpoint(request: Request) -> Any:
        form: dict[str, str] = {}
        if request.method == "POST":
            body = (await request.body()).decode("utf-8", errors="replace")
            form = dict(parse_qsl(body, keep_blank_values=True))
        resp = ui_handle(
            services.get(),
            request.method,
            request.url.path[len(UI) :] or "/",
            query=dict(request.query_params),
            form=form,
            token=request.cookies.get(COOKIE),
        )
        if resp.redirect:
            out: Any = RedirectResponse(resp.redirect, status_code=303)
        else:
            html = render(resp.template or "error.html", resp.context)
            out = HTMLResponse(html, status_code=resp.status)
        out.headers["Cache-Control"] = "no-store"
        out.headers["X-Content-Type-Options"] = "nosniff"
        out.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        if resp.set_token:
            out.set_cookie(
                COOKIE,
                resp.set_token,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
                path=UI,
                max_age=12 * 3600,
            )
        if resp.clear_token:
            out.delete_cookie(COOKIE, path=UI)
        return out

    for route in UI_ROUTES:
        path = UI + (route.path if route.path != "/" else "/")
        app.add_api_route(
            path, endpoint, methods=[route.method], include_in_schema=False, name=f"ui_{route.name}"
        )
    app.add_api_route(UI, endpoint, methods=["GET"], include_in_schema=False, name="ui_root")


__all__ = ["environment", "mount", "render"]
