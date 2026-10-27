#!/usr/bin/env bash
# Run a command inside the Playwright Python image, with a HEADED browser on a virtual
# display (xvfb). Nothing opens on your screen, and sites that refuse a headless
# browser's user agent still see a normal one.
#
#   scripts/run-in-container.sh pytest -m smoke --headed
#   scripts/run-in-container.sh scripts/run-parallel.sh --env qa
#
# The checkout is mounted at /work. One named volume holds the login token cache and
# the user-pool lease directory, so leases and cached sessions are shared by every
# container run (and only by them: the host keeps its own). A second one holds the
# Python environment, rebuilt when requirements.txt changes.
#
# Environment passed through: TEST_ENV, AUTH_MODE, TEST_RUN_ID and the user-pool and
# run-trust settings. Names only: a variable whose name contains a dot does not survive
# xvfb-run (it starts the command through /bin/sh, which drops such names).
#
# The image tag must equal the one in Dockerfile and the CI workflow (a test checks it).
set -euo pipefail

IMAGE="mcr.microsoft.com/playwright/python:v1.58.0-noble"
CACHE_VOLUME="${RUNNER_CACHE_VOLUME:-aurseal-runner-cache}"
VENV_VOLUME="${RUNNER_VENV_VOLUME:-aurseal-runner-venv}"

if [ $# -eq 0 ]; then
    echo "usage: scripts/run-in-container.sh <command> [args...]" >&2
    exit 2
fi

# Before anything else: without a running Docker nothing below can work.
if ! command -v docker >/dev/null 2>&1; then
    echo "run-in-container: docker is not installed (or not on PATH)" >&2
    exit 3
fi
if ! docker info >/dev/null 2>&1; then
    echo "run-in-container: Docker is not running; start it and try again" >&2
    exit 3
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

TTY_FLAGS="-i"
if [ -t 0 ] && [ -t 1 ]; then
    TTY_FLAGS="-it"
fi

# Runs inside the container: a venv on the shared volume (reusing the image's
# Playwright and browsers), refreshed only when requirements.txt changed, then the
# command on a virtual display.
BOOTSTRAP='
set -euo pipefail
if [ ! -x /venv/bin/python ]; then
    python -m venv --system-site-packages /venv
fi
want="$(sha256sum requirements.txt | cut -d" " -f1)"
have="$(cat /venv/.requirements.sha256 2>/dev/null || true)"
if [ "$want" != "$have" ]; then
    /venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt
    echo "$want" > /venv/.requirements.sha256
fi
export PATH="/venv/bin:$PATH"
exec xvfb-run -a --server-args="-screen 0 1920x1080x24" "$@"
'

exec docker run --rm "$TTY_FLAGS" --ipc=host \
    -v "$ROOT":/work -w /work \
    -v "$CACHE_VOLUME":/cache \
    -v "$VENV_VOLUME":/venv \
    -e RUNNER_IN_CONTAINER=1 \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e USER_POOL_LEASE_DIR=/cache/leases \
    -e TEST_AUTH_CACHE_DIR=/cache/auth \
    -e TEST_ENV="${TEST_ENV:-qa}" \
    -e AUTH_MODE \
    -e TEST_RUN_ID \
    -e USER_POOL_WAIT_S \
    -e USER_POOL_POLL_S \
    -e RUN_TRUST \
    -e RUN_TRUST_MIN_EXECUTED \
    -e BROWSER_TIMEZONE \
    "$IMAGE" bash -c "$BOOTSTRAP" bash "$@"
