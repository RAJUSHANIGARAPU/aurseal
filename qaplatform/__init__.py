"""Platform layer: a versioned REST API, token auth and a service over the results store.

``qaplatform.service.PlatformService`` is the product; the HTTP API and the UI are thin
adapters over it. Optional dependencies (web framework, server, templates) are listed in
``requirements-platform.txt`` and imported lazily, so nothing here needs them unless a
server is started.
"""
