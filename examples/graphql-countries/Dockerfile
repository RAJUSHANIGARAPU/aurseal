FROM python:3.12-slim

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

RUN useradd --create-home --uid 1000 tester && chown -R tester /app
USER tester

CMD ["pytest", "-n", "auto"]
