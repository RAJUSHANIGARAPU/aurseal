# Same Playwright version as pyproject.toml, so the browsers in the image match the client.
FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

COPY --from=ghcr.io/astral-sh/uv:0.9.4 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Dependencies first: this layer is cached until the lock file changes.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev

COPY . .

RUN chown -R pwuser /app
USER pwuser

CMD ["pytest", "-n", "auto"]
