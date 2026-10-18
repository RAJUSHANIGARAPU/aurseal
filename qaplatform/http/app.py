"""FastAPI adapter: mounts :data:`~qaplatform.http.routes.ROUTES` and nothing else.

The web framework is imported here only (``requirements-platform.txt``), inside
:func:`create_app`, so importing :mod:`qaplatform` never needs it. Each route is a thin
endpoint that hands the raw request to the framework-free pipeline
:func:`qaplatform.http.routes.handle`; FastAPI's own request models and generated OpenAPI
are switched off because the JSON Schemas are the contract.

SQLite connections belong to the thread that opened them, so the app does not share one
service object: ``service_factory`` is called once per serving thread.

No ``from __future__ import annotations`` here: FastAPI reads the endpoint's ``Request``
annotation at runtime, and the class is imported lazily inside :func:`create_app`.
"""

import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from qaplatform.http import routes as api_routes

ServiceFactory = Callable[[], Any]


class _PerThread:
    def __init__(self, factory: ServiceFactory) -> None:
        self.factory = factory
        self.local = threading.local()

    def get(self) -> Any:
        svc = getattr(self.local, "service", None)
        if svc is None:
            svc = self.factory()
            self.local.service = svc
        return svc


def to_framework_response(resp: api_routes.Response) -> Any:
    from fastapi.responses import Response

    body = json.dumps(resp.payload, ensure_ascii=False).encode("utf-8")
    return Response(
        content=body, status_code=resp.status, media_type=resp.content_type, headers=resp.headers
    )


def create_app(service_factory: ServiceFactory) -> Any:
    """The ASGI application: the REST API under ``/api/v1`` and, when Jinja2 is installed,
    the read-only UI under ``/ui``."""
    from fastapi import FastAPI, Request

    services = _PerThread(service_factory)
    app = FastAPI(title="Test results platform", openapi_url=None, docs_url=None, redoc_url=None)
    app.state.services = services

    def endpoint_for(route: api_routes.Route) -> Callable[..., Any]:
        async def endpoint(request: Request) -> Any:
            raw = await request.body() if route.body is not None else b""
            relative = request.url.path[len(api_routes.PREFIX) :]
            resp = api_routes.handle(
                services.get(),
                request.method,
                relative,
                query=dict(request.query_params),
                headers=dict(request.headers),
                body=raw,
                client_ip=request.client.host if request.client else None,
            )
            return to_framework_response(resp)

        endpoint.__name__ = route.name
        return endpoint

    for route in api_routes.ROUTES:
        app.add_api_route(
            route.full_path,
            endpoint_for(route),
            methods=[route.method],
            include_in_schema=False,
            name=route.name,
        )

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Exception) -> Any:
        resp = api_routes.handle(services.get(), request.method, request.url.path)
        return to_framework_response(resp)

    @app.exception_handler(405)
    async def not_allowed(request: Request, exc: Exception) -> Any:
        resp = api_routes.handle(services.get(), request.method, request.url.path)
        return to_framework_response(resp)

    try:
        import jinja2  # noqa: F401  (the UI is optional: the API works without it)
    except ImportError:
        pass
    else:
        from qaplatform.http.ui import mount

        mount(app, services)
    return app


def serve(service_factory: ServiceFactory, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the app with uvicorn (binds loopback unless told otherwise)."""
    import uvicorn

    uvicorn.run(create_app(service_factory), host=host, port=port, log_level="info")


@contextmanager
def serve_in_thread(
    service_factory: ServiceFactory, *, host: str = "127.0.0.1", port: int = 0
) -> Iterator[str]:
    """Run the app in a background thread (tests, local tools); yields the base URL.

    ``port=0`` picks a free port. The server is stopped when the block exits.
    """
    import uvicorn

    if port == 0:
        with socket.socket() as probe:
            probe.bind((host, 0))
            port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(service_factory), host=host, port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("the platform server did not start")
        time.sleep(0.02)
    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


__all__ = ["create_app", "serve", "serve_in_thread"]
