"""find_carrier: the artefact on a later version, an unreadable version, an absent artefact."""

from __future__ import annotations

import pytest

from test_data.versions import Absent, Carrier, NotReadable, VersionNotReadable, find_carrier


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def search(versions, read, *, wait_s=5, has=lambda doc: "invoice" in doc["documents"]):
    clock = FakeClock()
    return find_carrier(
        versions, read, has, wait_s=wait_s, interval_s=1, clock=clock, sleep=clock.sleep
    )


def test_artefact_on_the_later_scheduled_version_is_found():
    docs = {"v1": {"documents": []}, "v2": {"documents": ["invoice"]}}
    found = search(lambda: ["v1", "v2"], docs.__getitem__)
    assert isinstance(found, Carrier) and found.version == "v2"


def test_artefact_that_lands_late_is_found_on_a_later_round():
    rounds = {"n": 0}

    def read(version):
        rounds["n"] += 1
        return {"documents": ["invoice"] if rounds["n"] >= 5 else []}

    found = search(lambda: ["v1", "v2"], read)
    assert isinstance(found, Carrier) and found.version == "v1" and found.reads == 5


def test_a_version_added_while_waiting_is_searched():
    listed = iter([["v1"], ["v1"], ["v1", "v2"]])
    last = [["v1"]]

    def versions():
        last[0] = next(listed, last[0])
        return last[0]

    docs = {"v1": {"documents": []}, "v2": {"documents": ["invoice"]}}
    found = search(versions, docs.__getitem__)
    assert isinstance(found, Carrier) and found.version == "v2"


def test_unreadable_future_version_abstains_instead_of_failing():
    def read(version):
        if version == "v2":
            raise VersionNotReadable("starts 2031-01-01")
        return {"documents": []}

    found = search(lambda: ["v1", "v2"], read)
    assert isinstance(found, NotReadable)
    assert found.unreadable == ("v2",) and "starts 2031-01-01" in found.reason


def test_absent_lists_every_version_searched():
    found = search(lambda: ["v1", "v2"], lambda _v: {"documents": []}, wait_s=2)
    assert isinstance(found, Absent) and found.searched == ("v1", "v2")
    assert "searched v1, v2" in found.describe()


def test_a_harness_error_propagates():
    def read(_version):
        raise KeyError("no such field")

    with pytest.raises(KeyError):
        search(lambda: ["v1"], read)
