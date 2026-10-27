"""Observations in, one worded verdict out: outcome classifiers for multi-answer checks.

Some checks can end in more than two ways, and the additional endings are the
risky ones. ``assert refusal_shown`` merges "the input could not be edited at all"
and "the product let a forbidden change through" into one red with one
message. One of those proves nothing; the other is the defect the check exists
to find.

So observation and judgement are split. The page object or client records what
it saw into an observations object, and a classifier turns that into exactly
one named :class:`Verdict` whose message states the finding in words. The
decision is pure: it runs offline, it is unit-tested branch by branch, and it
can be argued about without an environment.

Every verdict state carries two flags:

* ``is_verdict``: the run exercised the subject, so the outcome says something
  about the product. ``False`` is an abstention ("screen not reached", "probe
  not run") and :meth:`Verdict.report` routes it to
  :meth:`EvidenceVerdict.no_verdict`, so it is counted, never a pass.
* ``passed``: the single outcome that may be reported as a pass.

Three classifiers ship here:

* :class:`PriceSurfaceClassifier`: a characterisation. It pins what a pricing
  screen looks like today and fires when a price surface appears, a guard
  stops guarding, or the recorded characterisation drifts.
* :func:`step_outcome`: "was the required block filled?" crossed with "did the
  step advance or get blocked?". A block is inferred only from a positive
  rejection signal.
* :func:`refusal_outcome`: a forbidden change inside an edit flow. Only
  ``REFUSED`` passes.
* :func:`submit_rule_outcome`: a validation that fires on submit, judged from a
  :class:`ui.components.submit_outcome.SubmitResult`. An unmet precondition (another
  required input empty, the probe value gone before the click) is an abstention.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, Protocol, TypeVar

from verification.evidence_verdict import EvidenceVerdict

ObsT = TypeVar("ObsT")


class OutcomeState(Enum):
    """Base for a classifier's named states: ``MEMBER = (is_verdict, passed)``.

    Values are numbered on definition, so two states with the same flags stay
    distinct members instead of collapsing into aliases.
    """

    def __new__(cls, is_verdict: bool, passed: bool) -> OutcomeState:
        member = object.__new__(cls)
        member._value_ = len(cls.__members__) + 1
        member.is_verdict = is_verdict
        member.passed = passed
        return member

    @property
    def demands_follow_up(self) -> bool:
        """A statement about the product that is not a pass: someone has to read it."""
        return self.is_verdict and not self.passed


@dataclass(frozen=True)
class Verdict(Generic[ObsT]):
    """One named outcome, the sentence that explains it, and what it was derived from."""

    state: OutcomeState
    message: str
    observations: ObsT | None = None

    @property
    def name(self) -> str:
        return self.state.name

    @property
    def is_verdict(self) -> bool:
        return self.state.is_verdict

    @property
    def passed(self) -> bool:
        return self.state.passed

    @property
    def demands_follow_up(self) -> bool:
        return self.state.demands_follow_up

    def report(self, coverage: EvidenceVerdict) -> None:
        """Turn the verdict into a test outcome.

        An abstention goes through ``coverage.no_verdict`` (skip, or fail when a
        verdict is required); a pass is recorded as ``proved``; anything else
        fails with the worded message.
        """
        if not self.is_verdict:
            coverage.no_verdict(f"{self.name}: {self.message}")
        if self.passed:
            coverage.proved(f"{self.name}: {self.message}")
            return
        raise AssertionError(f"{self.name}: {self.message}")

    def __str__(self) -> str:
        return f"{self.name}: {self.message}"


class OutcomeClassifier(ABC, Generic[ObsT]):
    """Decide one :class:`Verdict` from one observations object. Subclasses stay pure."""

    @abstractmethod
    def classify(self, observations: ObsT) -> Verdict[ObsT]: ...

    def __call__(self, observations: ObsT) -> Verdict[ObsT]:
        return self.classify(observations)


def _clean(value: str | None) -> str:
    return "" if value is None else value.strip()


def _clean_list(values: Iterable[str] | None) -> tuple[str, ...]:
    return tuple(_clean(v) for v in (values or ()) if v is not None)


def _normalise(label: str | None) -> str:
    """Lower-case, punctuation and spaces dropped, ``+`` kept."""
    return re.sub(r"[^a-z0-9+]", "", (label or "").lower())


# ---------------------------------------------------------------------------
# characterisation: a pricing screen that must stay free-form
# ---------------------------------------------------------------------------


class PriceSurfaceState(OutcomeState):
    NO_PRICE_SURFACE = (True, True)
    PRICE_SURFACE_APPEARED = (True, False)
    ZERO_PRICE_ACCEPTED = (True, False)
    ZERO_PRICE_REFUSAL_DID_NOT_BLOCK = (True, False)
    ZERO_PRICE_REFUSED_BY_SOMETHING_ELSE = (True, False)
    ARBITRARY_PRICE_REFUSED = (True, False)
    DESCRIPTION_OUTDATED = (True, False)
    SCREEN_NOT_REACHED = (False, False)
    BLOCK_NOT_READ = (False, False)
    PRICE_PROBE_NOT_RUN = (False, False)


@dataclass(frozen=True)
class PriceSurfaceExpectation:
    """What the screen looked like when the characterisation was written.

    ``surface_tokens`` are normalised fragments that would mean the product
    gained a price table or version surface. ``lookalikes`` are labels that
    contain such a fragment and are known not to be one; they are excluded by
    exact normalised name *before* any token is matched, because a false
    "the feature exists" is the worst outcome a characterisation can produce.
    """

    plan_value: str = "Standard"
    add_on_labels: tuple[str, ...] = ("Extended warranty", "Express delivery", "Gift wrap")
    zero_price_refusal: str = "Price must be greater than 0."
    surface_tokens: tuple[str, ...] = (
        "tier",
        "pricetable",
        "ratetable",
        "rateversion",
        "pricelist",
        "version",
    )
    lookalikes: tuple[str, ...] = ("Schema version", "Quote version", "Version")


@dataclass(frozen=True)
class PriceProbe:
    """Both halves of the price pair: an arbitrary figure, then zero."""

    attempted: bool = False
    arbitrary_price: str = ""
    arbitrary_accepted: bool = False
    zero_attempted: bool = False
    zero_blocked: bool = False
    zero_messages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "arbitrary_price", _clean(self.arbitrary_price))
        object.__setattr__(self, "zero_messages", _clean_list(self.zero_messages))

    @property
    def pair_exercised(self) -> bool:
        return self.attempted and self.zero_attempted


@dataclass(frozen=True)
class PriceSurfaceObservations:
    """What the pricing block showed. Label lists are scoped to the block itself."""

    screen_reached: bool
    plan_editable: bool = False
    plan_allowed_values: Sequence[str] = ()
    add_on_labels: Sequence[str] = ()
    other_field_labels: Sequence[str] = ()
    probe: PriceProbe = field(default_factory=PriceProbe)

    def __post_init__(self) -> None:
        for name in ("plan_allowed_values", "add_on_labels", "other_field_labels"):
            object.__setattr__(self, name, _clean_list(getattr(self, name)))
        if self.probe is None:
            object.__setattr__(self, "probe", PriceProbe())

    @property
    def block_read(self) -> bool:
        return bool(self.plan_allowed_values)


class PriceSurfaceClassifier(OutcomeClassifier[PriceSurfaceObservations]):
    """Pins a free-form pricing screen and fires the day it stops being one.

    The proof that no price table is consulted is a *pair*: an arbitrary figure
    is accepted and zero is refused by the floor rule's own message. A rated
    engine could not accept any figure; one accepting any figure would not need
    a floor. Neither half proves it alone.

    Order is the argument: reachability, then whether anything was read, then a
    new price surface (it outranks every price outcome), then the price pair,
    then drift at the end, so a real defect cannot be mistaken for an outdated description.
    """

    def __init__(self, expectation: PriceSurfaceExpectation | None = None) -> None:
        self.expected = expectation or PriceSurfaceExpectation()
        self._lookalikes = {_normalise(label) for label in self.expected.lookalikes}

    def surface_token_in(self, label: str) -> str | None:
        normalised = _normalise(label)
        if not normalised or normalised in self._lookalikes:
            return None
        return next((t for t in self.expected.surface_tokens if t in normalised), None)

    def surface_signals(self, o: PriceSurfaceObservations) -> list[str]:
        signals = []
        if len(o.plan_allowed_values) > 1:
            signals.append(
                f"plan now offers {len(o.plan_allowed_values)} values "
                f"{list(o.plan_allowed_values)} instead of one"
            )
        for label in (*o.add_on_labels, *o.other_field_labels):
            token = self.surface_token_in(label)
            if token:
                signals.append(f"the block carries a field labelled {label!r} (matched {token!r})")
        return signals

    def drift_signals(self, o: PriceSurfaceObservations) -> list[str]:
        signals = []
        if o.plan_editable:
            signals.append("plan was editable, the characterisation records it read-only")
        if len(o.plan_allowed_values) == 1 and _normalise(o.plan_allowed_values[0]) != _normalise(
            self.expected.plan_value
        ):
            signals.append(
                f"the single plan value read {o.plan_allowed_values[0]!r}, "
                f"not {self.expected.plan_value!r}"
            )
        if set(o.add_on_labels) != set(self.expected.add_on_labels):
            signals.append(
                f"the add-ons are {list(o.add_on_labels)}, not {list(self.expected.add_on_labels)}"
            )
        return signals

    def _rendered(self, o: PriceSurfaceObservations) -> str:
        p = o.probe
        return (
            f"Observed: plan={list(o.plan_allowed_values)} (editable={o.plan_editable}), "
            f"add-ons={list(o.add_on_labels)}, other={list(o.other_field_labels)}, "
            f"arbitrary={p.arbitrary_price!r} accepted={p.arbitrary_accepted}, "
            f"zero blocked={p.zero_blocked} messages={list(p.zero_messages)}."
        )

    def classify(self, o: PriceSurfaceObservations) -> Verdict[PriceSurfaceObservations]:
        s = PriceSurfaceState
        refusal = self.expected.zero_price_refusal

        def verdict(state: PriceSurfaceState, text: str) -> Verdict[PriceSurfaceObservations]:
            return Verdict(state, text, o)

        if not o.screen_reached:
            return verdict(
                s.SCREEN_NOT_REACHED,
                "NOT CHARACTERISED: the pricing block was never reached. A screen never "
                "opened shows the same absence as a product without a price surface, so this "
                "is not a pass.",
            )
        if not o.block_read:
            return verdict(
                s.BLOCK_NOT_READ,
                "NOT CHARACTERISED: the block was reached but no plan value was read. The plan "
                "is always filled in on a rendered screen, so the read failed; this is not "
                f"a product change. {self._rendered(o)}",
            )
        surface = self.surface_signals(o)
        if surface:
            return verdict(
                s.PRICE_SURFACE_APPEARED,
                "PREMISE IS STALE: the product now exposes a price surface: "
                f"{'; '.join(surface)}. Re-read the requirements against it. {self._rendered(o)}",
            )
        p = o.probe
        if not p.pair_exercised:
            return verdict(
                s.PRICE_PROBE_NOT_RUN,
                "NOT CHARACTERISED: the block holds, but the price pair (arbitrary accepted, zero "
                "refused) was not exercised, so 'no price table is consulted' is unsupported. "
                f"{self._rendered(o)}",
            )
        if not p.arbitrary_accepted:
            return verdict(
                s.ARBITRARY_PRICE_REFUSED,
                f"ARBITRARY PRICE REFUSED: {p.arbitrary_price!r} was refused, so something now "
                f"constrains the price. Read the refusal first. {self._rendered(o)}",
            )
        floor_fired = refusal in p.zero_messages
        if not p.zero_blocked:
            if floor_fired:
                return verdict(
                    s.ZERO_PRICE_REFUSAL_DID_NOT_BLOCK,
                    f'GUARD DOES NOT BLOCK: "{refusal}" appeared at zero and the flow advanced '
                    f"anyway. An error-level rule must block. {self._rendered(o)}",
                )
            return verdict(
                s.ZERO_PRICE_ACCEPTED,
                f'SILENT ACCEPTANCE: zero was submitted, "{refusal}" was never shown and the flow '
                f"advanced. The only rule guarding the price did not fire. {self._rendered(o)}",
            )
        if not floor_fired:
            return verdict(
                s.ZERO_PRICE_REFUSED_BY_SOMETHING_ELSE,
                f'BLOCKED BY SOMETHING ELSE: zero was held but "{refusal}" never appeared. '
                "An unrelated required field also stops the step from advancing, so the "
                f"block cannot be credited to the floor rule. {self._rendered(o)}",
            )
        drift = self.drift_signals(o)
        if drift:
            return verdict(
                s.DESCRIPTION_OUTDATED,
                f"CHARACTERISATION DRIFTED: {'; '.join(drift)}. Not a price surface, but the "
                f"written characterisation is out of date. {self._rendered(o)}",
            )
        return verdict(
            s.NO_PRICE_SURFACE,
            f"NO PRICE SURFACE, as characterised: one plan {list(o.plan_allowed_values)}, the "
            f"expected add-ons, {p.arbitrary_price!r} accepted and zero refused with "
            f'"{refusal}". The price is free-form, not rated.',
        )


# ---------------------------------------------------------------------------
# required block x step advance
# ---------------------------------------------------------------------------


class StepState(OutcomeState):
    FILLED_AND_ADVANCED = (True, True)
    FILLED_BUT_BLOCKED = (True, False)
    EMPTY_AND_BLOCKED = (True, False)
    EMPTY_AND_BLOCKED_WITHOUT_THE_MESSAGE = (True, False)
    EMPTY_BUT_ADVANCED = (True, False)
    NO_VERDICT_STEP_NEVER_RENDERED = (False, False)
    NO_VERDICT_BLOCK_ABSENT = (False, False)
    NO_VERDICT_SUBMIT_NOT_PRESSED = (False, False)
    NO_VERDICT_SUBMIT_RESULT_UNKNOWN = (False, False)


@dataclass(frozen=True)
class StepObservations:
    """One run of a step whose required block may or may not fill.

    ``fields_present`` counts the block's fields that were found: zero means
    "unreadable", not "empty". ``submit_status`` is ``-1`` when no response was
    seen. ``advanced`` must come from the *next* step rendering, never from this
    step disappearing.
    """

    case: str
    step_rendered: bool
    fields_present: int = 0
    block_value: str = ""
    submit_clicked: bool = False
    submit_status: int = -1
    advanced: bool = False
    rejection_message_shown: bool = False
    validation_surfaces: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "case", _clean(self.case))
        object.__setattr__(self, "block_value", _clean(self.block_value))
        object.__setattr__(self, "validation_surfaces", _clean_list(self.validation_surfaces))

    @property
    def block_filled(self) -> bool:
        return bool(self.block_value)

    @property
    def rejection_signal_seen(self) -> bool:
        """A *positive* reason to call the submit refused.

        Deliberately separate from ``advanced``: a broken harness yields "did not
        advance" without any product involvement.
        """
        return (
            self.rejection_message_shown
            or self.submit_status >= 400
            or bool(self.validation_surfaces)
        )


def join_block_values(labels: Sequence[str], values: Sequence[str | None]) -> str:
    """Join field values, treating a value that merely echoes its own label as empty.

    A generic reader that falls back to a field's text content reads an empty
    field as its label, which would silently turn "empty" into "filled".
    """
    if labels is None or values is None or len(labels) != len(values):
        raise ValueError(
            "labels and values must line up 1:1, got "
            f"{None if labels is None else len(labels)} label(s) and "
            f"{None if values is None else len(values)} value(s)"
        )
    kept = []
    for label, value in zip(labels, values, strict=True):
        text = _clean(value)
        if text and text.lower() != _clean(label).lower():
            kept.append(text)
    return " ".join(kept)


def expected_step_state(resolves: bool) -> StepState:
    """The outcome the configuration predicts: resolves -> fills and advances."""
    return StepState.FILLED_AND_ADVANCED if resolves else StepState.EMPTY_AND_BLOCKED


class StepOutcomeClassifier(OutcomeClassifier[StepObservations]):
    """Three "could not tell" checks first, then advancement, then a block only on evidence."""

    def __init__(self, rejection_message: str = "An address is required.") -> None:
        self.rejection_message = rejection_message

    def _rendered(self, o: StepObservations) -> str:
        status = "no response" if o.submit_status < 0 else f"HTTP {o.submit_status}"
        return (
            f"Observed: case={o.case!r}, rendered={o.step_rendered}, "
            f"fields={o.fields_present}, value={o.block_value!r}, clicked={o.submit_clicked}, "
            f"submit={status}, advanced={o.advanced}, "
            f"message={o.rejection_message_shown}, surfaces={list(o.validation_surfaces)}."
        )

    def classify(self, o: StepObservations) -> Verdict[StepObservations]:
        s = StepState
        tail = self._rendered(o)

        def verdict(state: StepState, text: str) -> Verdict[StepObservations]:
            return Verdict(state, f"{text} {tail}", o)

        if not o.step_rendered:
            return verdict(
                s.NO_VERDICT_STEP_NEVER_RENDERED,
                f"NOT EVALUATED for {o.case!r}: the step never rendered, nothing was read.",
            )
        if o.fields_present <= 0:
            return verdict(
                s.NO_VERDICT_BLOCK_ABSENT,
                f"NOT EVALUATED for {o.case!r}: the step carried none of the block's fields, so "
                "'empty' was unreadable rather than observed.",
            )
        if not o.submit_clicked:
            return verdict(
                s.NO_VERDICT_SUBMIT_NOT_PRESSED,
                f"NOT EVALUATED for {o.case!r}: the submit was never clicked, so whether the step "
                "can be submitted was not exercised.",
            )
        if o.advanced:
            if o.block_filled:
                return verdict(
                    s.FILLED_AND_ADVANCED,
                    f"FILLED AND ADVANCED for {o.case!r}: the block carried {o.block_value!r} "
                    "and the next step rendered.",
                )
            return verdict(
                s.EMPTY_BUT_ADVANCED,
                f"EMPTY BUT ADVANCED for {o.case!r}: the section was blank yet the step moved on "
                "anyway, so the record proceeds without the required data.",
            )
        if not o.rejection_signal_seen:
            return verdict(
                s.NO_VERDICT_SUBMIT_RESULT_UNKNOWN,
                f"NOT EVALUATED for {o.case!r}: the step did not advance and no rejection signal "
                "was seen. A stalled screen looks exactly like this.",
            )
        if o.block_filled:
            return verdict(
                s.FILLED_BUT_BLOCKED,
                f"BLOCKED DESPITE A FILLED BLOCK for {o.case!r}: something other than this block "
                "stopped the step, so it cannot be credited to the case.",
            )
        if o.rejection_message_shown:
            return verdict(
                s.EMPTY_AND_BLOCKED,
                f"EMPTY AND BLOCKED for {o.case!r}: the section was blank and the step was blocked "
                f'with "{self.rejection_message}".',
            )
        return verdict(
            s.EMPTY_AND_BLOCKED_WITHOUT_THE_MESSAGE,
            f"EMPTY AND BLOCKED, UNATTRIBUTED for {o.case!r}: refused, but "
            f'"{self.rejection_message}" never appeared.',
        )


def step_outcome(
    observations: StepObservations, rejection_message: str = "An address is required."
) -> Verdict[StepObservations]:
    return StepOutcomeClassifier(rejection_message).classify(observations)


# ---------------------------------------------------------------------------
# forbidden change inside an edit flow
# ---------------------------------------------------------------------------

DEFAULT_REFUSAL_MESSAGE = "This value cannot be changed."


class RefusalState(OutcomeState):
    REFUSED = (True, True)
    ACCEPTED_WITHOUT_NOTICE = (True, False)
    STOPPED_WITHOUT_MESSAGE = (True, False)
    REJECTED_YET_PROCEEDED = (True, False)
    INPUT_LOCKED = (False, False)
    CHANGE_IGNORED = (False, False)


@dataclass(frozen=True)
class RefusalObservations:
    """One attempt to make a forbidden change.

    ``field_editable`` is read *before* the attempt, so a guard that disables
    the field on refusal cannot pass for a read-only screen.
    """

    target: str
    value_before: str = ""
    value_after: str = ""
    field_editable: bool = True
    refusal_shown: bool = False
    advanced: bool = False
    validation_surfaces: Sequence[str] = ()

    def __post_init__(self) -> None:
        for name in ("target", "value_before", "value_after"):
            object.__setattr__(self, name, _clean(getattr(self, name)))
        object.__setattr__(self, "validation_surfaces", _clean_list(self.validation_surfaces))

    @property
    def change_taken(self) -> bool:
        """The new value really landed: it contains the target *and* differs from before.

        "Contains" alone greens a record that already had the target; "differs"
        alone greens a selector that moved some other field.
        """
        return (
            bool(self.target)
            and self.target.lower() in self.value_after.lower()
            and self.value_after != self.value_before
        )


class RefusalClassifier(OutcomeClassifier[RefusalObservations]):
    """Editability, then the change landing, then the two independent product signals."""

    def __init__(self, expected_message: str = DEFAULT_REFUSAL_MESSAGE) -> None:
        if not expected_message or not expected_message.strip():
            raise ValueError("a refusal classifier needs the exact message it expects")
        self.expected_message = expected_message

    def classify(self, o: RefusalObservations) -> Verdict[RefusalObservations]:
        s = RefusalState
        msg = self.expected_message
        change = f"{o.value_before!r} -> {o.value_after!r}"
        tail = (
            f"Observed: target={o.target!r}, before={o.value_before!r}, "
            f"after={o.value_after!r}, advanced={o.advanced}, "
            f"surfaces={list(o.validation_surfaces)}."
        )

        def verdict(state: RefusalState, text: str) -> Verdict[RefusalObservations]:
            return Verdict(state, text, o)

        if not o.field_editable:
            return verdict(
                s.INPUT_LOCKED,
                "NOT EVALUATED: the field carried no interactable control, so the change could "
                f"not be attempted and nothing is known about the guard. {tail}",
            )
        if not o.change_taken:
            return verdict(
                s.CHANGE_IGNORED,
                f"NOT EVALUATED: the field never took {o.target!r}, so no change was submitted. "
                f"The option is absent or the selector hit another field. {tail}",
            )
        if o.refusal_shown and not o.advanced:
            return verdict(
                s.REFUSED,
                f'REFUSED as required: changed {change} and "{msg}" was raised without advancing.',
            )
        if o.refusal_shown:
            return verdict(
                s.REJECTED_YET_PROCEEDED,
                f'GUARD DOES NOT BLOCK: "{msg}" appeared after {change}, but the flow still '
                f"advanced. An error-level rule must block, not annotate. {tail}",
            )
        if o.advanced:
            return verdict(
                s.ACCEPTED_WITHOUT_NOTICE,
                f'SILENT ACCEPTANCE: changed {change}, "{msg}" was never shown and the flow '
                f"advanced. The guard stayed silent: a product defect, not a harness one. {tail}",
            )
        return verdict(
            s.STOPPED_WITHOUT_MESSAGE,
            f'BLOCKED BY SOMETHING ELSE: changed {change}, the flow held, but "{msg}" never '
            f"appeared, so the block cannot be credited to this guard. {tail}",
        )


def refusal_outcome(
    observations: RefusalObservations, expected_message: str = DEFAULT_REFUSAL_MESSAGE
) -> Verdict[RefusalObservations]:
    return RefusalClassifier(expected_message).classify(observations)


# ---------------------------------------------------------------------------
# a validation that fires on submit
# ---------------------------------------------------------------------------


class SubmitObservation(Protocol):
    """What :func:`submit_rule_outcome` reads; ``SubmitResult`` satisfies it."""

    exercised: bool
    not_exercised_reason: str | None
    status: int | None
    advanced: bool | None

    @property
    def rejected(self) -> bool: ...

    def mentions(self, text: str) -> bool: ...

    def describe(self) -> str: ...


class SubmitRuleState(OutcomeState):
    REFUSED_AT_SUBMIT = (True, True)
    REFUSED_WITHOUT_THE_MESSAGE = (True, False)
    ACCEPTED = (True, False)
    NOT_EXERCISED = (False, False)
    NO_VERDICT_OUTCOME_UNKNOWN = (False, False)


def submit_rule_outcome(
    result: SubmitObservation, expected_message: str | None = None
) -> Verdict[SubmitObservation]:
    """Refused (with ``expected_message`` when given), accepted, or no verdict at all."""
    s = SubmitRuleState
    tail = f"Observed: {result.describe()}."
    if not result.exercised:
        return Verdict(
            s.NOT_EXERCISED,
            f"NOT EXERCISED: {result.not_exercised_reason}; nothing was submitted. {tail}",
            result,
        )
    if result.rejected:
        if expected_message and not result.mentions(expected_message):
            return Verdict(
                s.REFUSED_WITHOUT_THE_MESSAGE,
                f'REFUSED, UNATTRIBUTED: the submit was refused but "{expected_message}" never '
                f"appeared, so the refusal cannot be credited to this rule. {tail}",
                result,
            )
        return Verdict(s.REFUSED_AT_SUBMIT, f"REFUSED AT SUBMIT as required. {tail}", result)
    if result.advanced or (result.status is not None and 200 <= result.status < 300):
        return Verdict(
            s.ACCEPTED,
            f"ACCEPTED: the bad value was submitted and nothing refused it. {tail}",
            result,
        )
    return Verdict(
        s.NO_VERDICT_OUTCOME_UNKNOWN,
        f"NOT EVALUATED: no rejection signal and no sign of acceptance. {tail}",
        result,
    )


__all__ = [
    "DEFAULT_REFUSAL_MESSAGE",
    "OutcomeClassifier",
    "OutcomeState",
    "PriceProbe",
    "PriceSurfaceClassifier",
    "PriceSurfaceExpectation",
    "PriceSurfaceObservations",
    "PriceSurfaceState",
    "RefusalClassifier",
    "RefusalObservations",
    "RefusalState",
    "StepObservations",
    "StepOutcomeClassifier",
    "StepState",
    "SubmitObservation",
    "SubmitRuleState",
    "Verdict",
    "expected_step_state",
    "join_block_values",
    "refusal_outcome",
    "step_outcome",
    "submit_rule_outcome",
]
