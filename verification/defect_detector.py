"""Expected-red detectors: run a correct-behaviour oracle against an open defect.

The imperative sibling of the ``known_defect`` marker, for detectors that need a
positive control. The oracle asserts the CORRECT behaviour; while the defect is
open it fails, and ``still_reproduces`` inverts the reporting, never the
assertion::

    still_reproduces(
        "BUG-142",
        r"split totals differ by \\d+ cent",
        oracle=lambda: assert_totals_add_up(invoice),
        exercised=lambda: assert_invoice_was_read(invoice),
    )

==========================================  ======================  ==========
what happened                               verdict                 test
==========================================  ======================  ==========
control failed or skipped                   held_empty           skip
oracle skipped / raised a non-assertion     held_empty           re-raised
oracle failed, message matches signature    reproduces              pass
oracle failed, message does not match       signature_mismatch      FAIL
oracle passed                               cleared                 FAIL
defect closed, oracle passed                closed_pass             pass
defect closed, message matches signature    closed_regressed        FAIL
==========================================  ======================  ==========

Why each rail exists:

* **Positive control.** A detector infers "still broken" from the *absence* of
  correct behaviour, and a harness break (a read that returned ``""``, a page
  that never loaded) produces the identical absence. ``exercised`` must prove
  the mechanism ran before any verdict counts; a failing control is "could not
  tell", never "still reproduces". It is a required argument: pass ``None``
  only together with ``no_control_reason`` saying why the oracle cannot be
  fooled by a no-op.
* **Signature tightness.** Detectors tend to prefix every assertion with the
  defect id, so a signature that is just the id matches setup failures too and
  the detector becomes a permanent false green. The signature must carry at
  least ``SIGNATURE_MIN_LENGTH`` characters beyond any defect id and
  punctuation: key it to the verdict assertion's own wording.
* **Cleared is a failure.** A green "it is fixed" would sit in a report
  forever; a red one makes someone verify the fix and unwrap the detector so
  the oracle stands as an ordinary regression test.
* **Closed defects.** Ids in ``KNOWN_DEFECTS_CLOSED`` (the same source the
  ``known_defect`` marker reads) are expected fixed: the oracle must pass, and
  a reproduction is a regression on a closed defect.

``attribute_failure`` sorts a rejection into defect / environment /
unattributable before a detector turns it into a verdict.

Every outcome is recorded through ``verification.verdicts``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Iterable
from enum import Enum
from pathlib import Path

import pytest

from verification.verdicts import Kind, RedVerdict, record_verdict

logger = logging.getLogger(__name__)

CLOSED_ENV = "KNOWN_DEFECTS_CLOSED"
STATUS_FILE_ENV = "DEFECT_STATUS_FILE"  # tracker snapshot written by traceability.defects
REQUIREMENT_PREFIXES_ENV = "REQUIREMENT_ID_PREFIXES"  # shared with traceability.requirements
DEFAULT_REQUIREMENT_PREFIXES = ("REQ",)
SIGNATURE_MIN_LENGTH = 12

OPEN_TAG = "KNOWN DEFECT STILL OPEN"
FIXED_TAG = "KNOWN DEFECT CLEARED"
REGRESSED_MARKER = "EXPECTED-RED REGRESSED ON A CLOSED DEFECT"

_DEFECT_ID = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*-\d+\b")
_NOISE = re.compile(r"[\W_]+")

Check = Callable[[], object]


def closed_defects() -> frozenset[str]:
    """Closed defect ids: ``KNOWN_DEFECTS_CLOSED`` (comma separated) plus, when
    ``DEFECT_STATUS_FILE`` names a tracker snapshot (``python -m traceability defects
    sync``), every id that snapshot records as closed. An id in neither is open.
    """
    closed = {c.strip() for c in os.environ.get(CLOSED_ENV, "").split(",") if c.strip()}
    return frozenset(closed | _snapshot_closed(os.environ.get(STATUS_FILE_ENV, "").strip()))


def _snapshot_closed(path: str) -> set[str]:
    if not path:
        return set()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {
            str(k)
            for k, v in (data.get("defects") or {}).items()
            if isinstance(v, dict) and v.get("state") == "closed"
        }
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning("[expected-red] unreadable defect snapshot %s (%s): all open", path, exc)
        return set()


def require_defect_id(ticket: str) -> str:
    """``ticket`` stripped; raises ``ValueError`` for a blank id or a requirement id.

    ``REQ-12`` names what must hold, not what breaks it: accepting it as a defect id would
    let a detector report a requirement as "still reproducing".
    """
    if not isinstance(ticket, str) or not ticket.strip():
        raise ValueError("an expected-red detector needs a defect id")
    raw = os.environ.get(REQUIREMENT_PREFIXES_ENV, "")
    prefixes = {p.strip() for p in raw.split(",") if p.strip()} or set(DEFAULT_REQUIREMENT_PREFIXES)
    head = ticket.strip().split("-", 1)[0]
    if "-" in ticket and head in prefixes:
        raise ValueError(
            f"{ticket!r} is a requirement id, not a defect id; link requirements with "
            f"@pytest.mark.requirement and defects with their tracker id"
        )
    return ticket.strip()


def require_usable_signature(ticket: str, signature: str) -> re.Pattern[str]:
    """Compile ``signature`` (DOTALL), rejecting one too loose to identify the defect.

    Raises ``ValueError`` for a blank or invalid pattern, or one with fewer than
    ``SIGNATURE_MIN_LENGTH`` word characters once defect ids are removed.
    """
    require_defect_id(ticket)
    if not isinstance(signature, str) or not signature.strip():
        raise ValueError(
            f"{ticket}: the failure signature must not be blank; an empty signature matches "
            f"any failure, so the detector would stay green whatever happened"
        )
    residue = _NOISE.sub("", _DEFECT_ID.sub("", signature.replace(ticket, "")))
    if len(residue) < SIGNATURE_MIN_LENGTH:
        raise ValueError(
            f"{ticket}: failure signature {signature!r} is too loose. A signature that is only a "
            f"defect id (plus punctuation) also matches setup failures and reports 'still "
            f"reproduces' for an unrelated failure. Use the wording of the verdict's own "
            f"assertion: at least {SIGNATURE_MIN_LENGTH} characters beyond the id"
        )
    try:
        return re.compile(signature, re.DOTALL)
    except re.error as exc:
        raise ValueError(f"{ticket}: failure signature is not a valid regex: {exc}") from exc


def still_reproduces(
    ticket: str,
    signature: str,
    oracle: Check,
    *,
    exercised: Check | None,
    no_control_reason: str = "",
    closed: bool | None = None,
) -> RedVerdict:
    """Run ``oracle`` as a detector for ``ticket`` and return the verdict reached.

    ``closed=None`` resolves the defect's state from ``KNOWN_DEFECTS_CLOSED``.
    Raises (fails or skips the test) for every verdict that is not green; see
    the module table.
    """
    pattern = require_usable_signature(ticket, signature)
    if exercised is None and not no_control_reason.strip():
        raise ValueError(
            f"{ticket}: a detector without a positive control must say why "
            f"(no_control_reason=...): an unexercised oracle reads as 'still reproduces'"
        )
    is_closed = ticket in closed_defects() if closed is None else closed

    def record(verdict: RedVerdict, detail: object) -> None:
        record_verdict(Kind.EXPECTED_RED, verdict, ticket=ticket, detail=detail)

    if exercised is not None:
        try:
            exercised()
        except pytest.skip.Exception as abort:
            record(RedVerdict.HELD_EMPTY, f"control skipped: {abort}")
            raise
        except Exception as exc:
            why = (
                f"{ticket} detector has no proof that it reached the code path under test, so "
                f"a verdict would be unfounded (a harness break produces the same absence of "
                f"correct behaviour): {exc!r}"
            )
            record(RedVerdict.HELD_EMPTY, why)
            pytest.skip(why)

    try:
        oracle()
    except pytest.skip.Exception as abort:
        record(RedVerdict.HELD_EMPTY, f"precondition not met: {abort}")
        raise
    except (AssertionError, pytest.fail.Exception) as failure:
        message = str(failure)
        if not pattern.search(message):
            record(RedVerdict.SIGNATURE_MISMATCH, message)
            raise AssertionError(
                f"{ticket} detector failed for an UNDOCUMENTED reason: it is not the tracked "
                f"defect and must not be reported as still open. Expected the oracle to fail "
                f"matching /{signature}/, got: {message}"
            ) from failure
        if is_closed:
            record(RedVerdict.CLOSED_REGRESSED, message)
            raise AssertionError(
                f"{REGRESSED_MARKER}: {ticket} is closed but the defect reproduces again. "
                f"Reopen it (or file its successor) with this observation: {message}"
            ) from failure
        logger.warning(
            "[expected-red] %s: %s still reproduces (passing only because the bug is still "
            "there; the feature itself is still broken): %s",
            OPEN_TAG,
            ticket,
            message,
        )
        record(RedVerdict.REPRODUCES, message)
        return RedVerdict.REPRODUCES
    except Exception as exc:
        # A harness or environment break says nothing about the defect: no verdict, and
        # the original exception propagates so it is triaged as what it is.
        record(RedVerdict.HELD_EMPTY, f"harness/environment failure: {exc!r}")
        raise

    if is_closed:
        record(RedVerdict.CLOSED_PASS, "oracle passed: the closed defect's fix holds")
        return RedVerdict.CLOSED_PASS
    record(RedVerdict.CLEARED, "oracle passed: the defect no longer reproduces")
    raise AssertionError(
        f"{FIXED_TAG}: {ticket} NO LONGER REPRODUCES. The correct-behaviour oracle now "
        f"passes, so the defect looks fixed. Verify it, close {ticket}, then drop the "
        f"still_reproduces wrapper so the oracle guards the fix as an ordinary test. This "
        f"failure is the expected signal from the detector."
    )


class Attribution(str, Enum):
    DEFECT = "defect"
    ENVIRONMENT = "environment"
    UNATTRIBUTABLE = "unattributable"


def attribute_failure(
    message: str,
    *,
    defect: Iterable[str],
    environment: Iterable[str],
) -> Attribution:
    """Decide whether a rejection is the tracked defect, before it becomes a verdict.

    A system under test often refuses a request for reasons that have nothing
    to do with the defect (a lock held elsewhere, a validator for a different
    rule), and its message may still echo the field the defect is
    about. So ``environment`` patterns win over ``defect`` patterns, and a
    message matching neither (or blank) is ``UNATTRIBUTABLE``: no verdict, not
    a guess. Only ``DEFECT`` may be reported as the defect reproducing.
    """
    text = message or ""
    if not text.strip():
        return Attribution.UNATTRIBUTABLE
    if any(re.search(p, text, re.IGNORECASE | re.DOTALL) for p in environment):
        return Attribution.ENVIRONMENT
    if any(re.search(p, text, re.IGNORECASE | re.DOTALL) for p in defect):
        return Attribution.DEFECT
    return Attribution.UNATTRIBUTABLE
