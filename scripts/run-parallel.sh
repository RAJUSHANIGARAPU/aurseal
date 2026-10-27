#!/usr/bin/env bash
# Run the gate selection as N parallel shard processes, then the serial lane, then merge.
#
#   scripts/run-parallel.sh [--env E] [--workers N] [--on-host] [--headless] [-- pytest args]
#
# By default it re-runs itself inside scripts/run-in-container.sh (headed browser on a
# virtual display); --on-host runs on this machine instead. Each worker logs in as a
# user of its own from the environment's user pool (.env.<env>.pool, see
# gate/user_pool.py), so the worker count defaults to min(pool size, 4) and can never
# exceed the pool size. An exported AUTH_USERNAME is unset for the run when a pool
# exists: one shared login is exactly what the pool is there to avoid.
#
# Every run starts from an empty output folder (reports/parallel) and leaves:
#   shard-<k>.log, junit-<k>.xml, allure-<k>/   per shard (k = 1..N, serial)
#   shards/                                     manifests for the merge
#   junit-merged.xml, allure-results/           the merged run
# and reports/durations.json, which balances the next run's shards.
#
# The run-trust executed floor (RUN_TRUST_MIN_EXECUTED) is split over the shards,
# rounded up; the serial lane gets 0, because it may legitimately run nothing.
#
# Works with the bash 3.2 that macOS ships (--on-host): no bash 4 features.
set -eu

usage() {
    cat <<'USAGE'
usage: scripts/run-parallel.sh [--env E] [--workers N] [--on-host] [--headless] [-- pytest args]

  --env E            target environment (default: $TEST_ENV, else qa)
  --workers N        parallel shards (default: min(pool size, 4); never more than the pool)
  --on-host          run on this machine instead of in the Playwright container
  --headless         headless browser (default: headed; on a virtual display in the container)
  --print-selection  print the marker expression the run selects, and exit
  -- ARGS            passed to every pytest process unchanged
USAGE
}

die() {
    echo "run-parallel: $*" >&2
    exit 2
}

ENV_NAME="${TEST_ENV:-qa}"
WORKERS=""
ON_HOST=0
HEADLESS=0
PRINT_SELECTION=0
OUT="reports/parallel"
EXTRA=()
ORIGINAL=("$@")

while [ $# -gt 0 ]; do
    case "$1" in
        --env)
            [ $# -ge 2 ] || die "--env needs a value"
            ENV_NAME="$2"
            shift 2
            ;;
        --env=*)
            ENV_NAME="${1#*=}"
            shift
            ;;
        --workers)
            [ $# -ge 2 ] || die "--workers needs a value"
            WORKERS="$2"
            shift 2
            ;;
        --workers=*)
            WORKERS="${1#*=}"
            shift
            ;;
        --on-host)
            ON_HOST=1
            shift
            ;;
        --headless)
            HEADLESS=1
            shift
            ;;
        --print-selection)
            PRINT_SELECTION=1
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        --)
            shift
            EXTRA=("$@")
            break
            ;;
        *)
            usage >&2
            die "unknown option $1"
            ;;
    esac
done

case "$WORKERS" in
    "" | *[!0-9]*) [ -z "$WORKERS" ] || die "--workers must be a positive number, got $WORKERS" ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ "$ON_HOST" = 0 ] && [ -z "${RUNNER_IN_CONTAINER:-}" ] && [ "$PRINT_SELECTION" = 0 ]; then
    export TEST_ENV="$ENV_NAME"
    exec scripts/run-in-container.sh scripts/run-parallel.sh ${ORIGINAL[@]+"${ORIGINAL[@]}"}
fi

if [ -z "${PY:-}" ]; then
    if [ -z "${RUNNER_IN_CONTAINER:-}" ] && [ -x .venv/bin/python ]; then
        PY=.venv/bin/python
    else
        PY=python
    fi
fi

MARKERS="$("$PY" -m gate.parallel_run selection)"
if [ "$PRINT_SELECTION" = 1 ]; then
    printf '%s\n' "$MARKERS"
    exit 0
fi

export TEST_ENV="$ENV_NAME"
export TEST_RUN_ID="${TEST_RUN_ID:-local-parallel-$(date +%Y%m%d-%H%M%S)}"

POOL_SIZE="$("$PY" -m gate.user_pool size --env "$ENV_NAME")"
if [ "$POOL_SIZE" -gt 0 ] && [ -n "${AUTH_USERNAME:-}" ]; then
    echo "run-parallel: AUTH_USERNAME is exported; unset for this run so each worker leases a pool user"
    unset AUTH_USERNAME AUTH_PASSWORD
fi
if [ -n "$WORKERS" ]; then
    N="$("$PY" -m gate.user_pool workers --env "$ENV_NAME" --requested "$WORKERS")"
else
    N="$("$PY" -m gate.user_pool workers --env "$ENV_NAME")"
fi
SHARD_FLOOR="$("$PY" -m gate.parallel_run floor --workers "$N")"

BROWSER_ARGS=()
if [ "$HEADLESS" = 0 ]; then
    BROWSER_ARGS=(--headed)
fi

# A fresh folder every run: a result left over from the last one must not count.
rm -rf "$OUT"
mkdir -p "$OUT/shards" "$OUT/leases"

echo "run-parallel: env $ENV_NAME, $N worker(s), pool of $POOL_SIZE, run $TEST_RUN_ID"
echo "run-parallel: selection -m \"$MARKERS\""

# run_shard <label> <K/N spec> <worker prefix> <executed floor>
run_shard() {
    TEST_WORKER_PREFIX="$3" \
        USER_POOL_LEASE_RECORD="$OUT/leases/shard-$1.lease.json" \
        RUN_TRUST_MIN_EXECUTED="$4" \
        "$PY" -m pytest -m "$MARKERS" -p gate.pytest_shard --shard="$2" --serial-lane \
        --shard-dir="$OUT/shards" -n 0 \
        --junitxml="$OUT/junit-$1.xml" --alluredir="$OUT/allure-$1" \
        ${BROWSER_ARGS[@]+"${BROWSER_ARGS[@]}"} ${EXTRA[@]+"${EXTRA[@]}"} \
        >"$OUT/shard-$1.log" 2>&1
}

PIDS=""
i=1
while [ "$i" -le "$N" ]; do
    run_shard "$i" "$i/$N" "w$i" "$SHARD_FLOOR" &
    PIDS="$PIDS $!"
    i=$((i + 1))
done

i=1
for pid in $PIDS; do
    if wait "$pid"; then code=0; else code=$?; fi
    echo "run-parallel: shard $i/$N finished, exit $code ($OUT/shard-$i.log)"
    i=$((i + 1))
done

# The serial lane starts only after every parallel shard has finished.
if run_shard serial "serial/$N" ws 0; then code=0; else code=$?; fi
echo "run-parallel: serial lane finished, exit $code ($OUT/shard-serial.log)"

LEASES=""
i=1
while [ "$i" -le "$N" ]; do
    LEASES="$LEASES $OUT/leases/shard-$i.lease.json"
    i=$((i + 1))
done
LEASES="$LEASES $OUT/leases/shard-serial.lease.json"

if "$PY" -m gate.merge --manifests "$OUT/shards" --shards "$N" --summary \
    --junit "$OUT"/junit-*.xml --junit-out "$OUT/junit-merged.xml" \
    --allure "$OUT"/allure-* --allure-out "$OUT/allure-results" \
    --durations-out reports/durations.json; then
    verdict=0
else
    verdict=$?
fi
# shellcheck disable=SC2086 # the lease paths contain no spaces
"$PY" -m gate.parallel_run leases $LEASES
exit "$verdict"
