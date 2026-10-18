"""
Prove that a text was never put in front of the user during a whole flow.

An ordinary assertion looks at the page once. A dialog that opens for a second
and is then confirmed, or a wizard step that is shown and skipped past, is gone
by the time the test looks, so "not visible now" says nothing about "never
offered". ``AbsenceWatch`` records instead: installed before the flow, it notes
every watched phrase that ever appears in the page's body text, across DOM
updates, same-origin navigations and reloads, and the assertions read that
record at the end.

How the recorder works (in the page):

* it is registered as an init script, so every new document in the page arms
  itself, and it is also evaluated once in the document already open;
* a ``MutationObserver`` (child lists, subtree, character data) schedules a
  check, throttled to at most four per second, and a one-second timer re-checks
  anyway for changes no observer event reports;
* a check reads ``document.body.textContent``: a plain, case-sensitive
  substring test. ``textContent`` is used rather than ``innerText`` because it
  does not force layout; the price is that text inside hidden elements counts
  as shown (see "choosing phrases" below);
* sightings live in ``sessionStorage`` under a key unique to this watch, so they
  survive reloads and same-origin navigations, and one watch can never read
  another's. Every storage access is wrapped; a failure never throws in the page.

Using it:

* pair every ``assert_never_seen`` with an ``assert_seen`` on a phrase the flow
  is known to show, on the same watch. A recorder that silently failed to arm
  sees nothing, and "nothing seen" is exactly what an absence assertion wants,
  so without the positive control a broken recorder passes every test;
* choose phrases only an actionable control carries (the label of the button
  that would confirm the unwanted step) rather than a heading, which may also
  sit in a progress indicator or a hidden template.

A change that appears and disappears within one throttle window (a quarter of a
second) can be missed; this is a recorder for steps a user is offered, not for
flashes.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable

from core.logger import logger

_PREFIX = "absence-watch:"

# Arm the recorder in one document. Placeholders are replaced with JSON literals.
_RECORDER = """(() => {
  const KEY = __KEY__;
  const PHRASES = __PHRASES__;
  const armed = (window.__absenceWatchArmed = window.__absenceWatchArmed || {});
  if (armed[KEY]) return;
  armed[KEY] = true;
  const load = () => {
    try {
      const value = JSON.parse(window.sessionStorage.getItem(KEY) || "[]");
      return Array.isArray(value) ? value : [];
    } catch (e) {
      return [];
    }
  };
  const save = (list) => {
    try {
      window.sessionStorage.setItem(KEY, JSON.stringify(list));
    } catch (e) {}
  };
  let pending = false;
  let last = 0;
  const check = () => {
    pending = false;
    last = Date.now();
    const body = document.body;
    if (!body) return;
    const text = body.textContent || "";
    const seen = load();
    let changed = false;
    for (const phrase of PHRASES) {
      if (!seen.includes(phrase) && text.includes(phrase)) {
        seen.push(phrase);
        changed = true;
      }
    }
    if (changed) save(seen);
  };
  const schedule = () => {
    if (pending) return;
    pending = true;
    setTimeout(check, Math.max(0, 250 - (Date.now() - last)));
  };
  const start = () => {
    save(load());
    check();
    new MutationObserver(schedule).observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    setInterval(check, 1000);
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})()"""

_CLEAR = """(prefix) => {
  try {
    const store = window.sessionStorage;
    for (let i = store.length - 1; i >= 0; i--) {
      const key = store.key(i);
      if (key && key.startsWith(prefix)) store.removeItem(key);
    }
  } catch (e) {}
}"""

_LIVE = """(key) => {
  const armed = window.__absenceWatchArmed || {};
  let stored = null;
  try {
    stored = window.sessionStorage.getItem(key);
  } catch (e) {}
  return armed[key] === true && stored !== null;
}"""

_READ = "(key) => window.sessionStorage.getItem(key)"


class AbsenceWatch:
    """Record whether watched phrases ever appeared in a page during a flow."""

    def __init__(self, page, phrases: Iterable[str]):
        self.page = page
        self.phrases = list(phrases)
        if not self.phrases:
            raise ValueError("AbsenceWatch needs at least one phrase to watch")
        if any(not isinstance(p, str) or not p for p in self.phrases):
            raise ValueError("every watched phrase must be a non-empty string")
        self.key = f"{_PREFIX}{uuid.uuid4().hex}"
        self.script = _RECORDER.replace("__KEY__", json.dumps(self.key)).replace(
            "__PHRASES__", json.dumps(self.phrases)
        )

    def install(self) -> AbsenceWatch:
        """Arm the recorder for every future document and for the one already open."""
        logger.info(f"absence watch: watching {self.phrases}")
        try:
            self.page.evaluate(_CLEAR, _PREFIX)
        except Exception as err:  # noqa: BLE001 - a page without storage has nothing to clear
            logger.warning(f"absence watch: could not clear earlier sightings: {err}")
        self.page.add_init_script(script=self.script)
        try:
            self.page.evaluate(self.script)
        except Exception as err:  # noqa: BLE001 - future documents are still armed
            logger.warning(f"absence watch: could not arm the current document: {err}")
        return self

    # ------------------------------------------------------------- reading

    def seen(self) -> list[str]:
        """The watched phrases observed so far, in the order they were first seen."""
        try:
            raw = self.page.evaluate(_READ, self.key)
        except Exception as err:  # noqa: BLE001
            logger.warning(f"absence watch: could not read sightings: {err}")
            return []
        if raw is None:
            return []
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("absence watch: stored sightings are not valid JSON")
            return []
        if not isinstance(value, list):
            logger.warning("absence watch: stored sightings are not a list")
            return []
        return [item for item in value if isinstance(item, str)]

    def was_seen(self, phrase: str) -> bool:
        return phrase in self.seen()

    # ---------------------------------------------------------- assertions

    def assert_live(self, reason: str) -> None:
        """Fail when the recorder is not running in the current document."""
        try:
            live = self.page.evaluate(_LIVE, self.key)
        except Exception as err:
            raise AssertionError(
                f"Absence watch cannot be trusted ({reason}): checking whether the "
                f"recorder is running failed, so its verdict is unreliable: {err}"
            ) from err
        if live is not True:
            raise AssertionError(
                f"Absence watch is not running in this document ({reason}). This is a "
                "test-harness problem, not a verdict on the product: an absence check "
                "against a recorder that is not running would pass for anything."
            )

    def assert_seen(self, phrase: str, reason: str) -> None:
        """Positive control: a phrase the flow is known to show must have been recorded."""
        self._watched(phrase)
        self.assert_live(reason)
        sightings = self.seen()
        if phrase not in sightings:
            raise AssertionError(
                f"Positive control failed: {phrase!r} was never recorded ({reason}). The "
                "recorder apparently did not observe the flow, so any absence verdict "
                f"from this watch is invalid. Recorded so far: {sightings}"
            )

    def assert_never_seen(self, phrase: str, reason: str) -> None:
        """Absence: the phrase must not have appeared at any point of the flow."""
        self._watched(phrase)
        self.assert_live(reason)
        sightings = self.seen()
        if phrase in sightings:
            raise AssertionError(
                f"{phrase!r} was shown during the flow, but must never be offered: "
                f"{reason}. Recorded: {sightings}"
            )

    def _watched(self, phrase: str) -> None:
        if phrase not in self.phrases:
            raise ValueError(f"{phrase!r} is not one of the watched phrases {self.phrases}")
