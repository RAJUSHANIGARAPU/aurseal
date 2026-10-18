"""Invoice notes in the outbound document data read back exactly as stored (detector pair)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from tests.demo_examples import detector_pair
from tests.demo_examples.messaging import support

TICKET = "DEMO-109"
SPECIAL = "R&D <pilot>"
PLAIN = "Remit by the end of the month"


def notes_in(outbound, sub) -> list[str]:
    (message,) = outbound.overview(sub["id"])
    support.generate_until_files(outbound, sub["id"], message["id"])
    xml = outbound.download(message["id"], fmt="xml").decode("utf-8")
    return [n.text or "" for n in ET.fromstring(xml).findall("./invoiceNotes/note")]


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_invoice_notes_round_trip_in_xml(demo_seed, demo_control, outbound, fault_on):
    demo_control.faults(*(["xml_escapes_twice"] if fault_on else []))
    sub = support.booked(demo_seed, invoice_notes=[{"text": SPECIAL}])
    carried = notes_in(outbound, sub)

    def exercised() -> None:
        # a note with nothing to escape round-trips with or without the fault
        plain = support.booked(demo_seed, invoice_notes=[{"text": PLAIN}])
        assert notes_in(outbound, plain) == [PLAIN]
        assert len(carried) == 1, f"one note was stored, the XML carries {carried}"

    def oracle() -> None:
        assert carried == [SPECIAL], f"{TICKET}: the note arrived as {carried[0]!r}"

    detector_pair.judge(
        demo_control,
        ticket=TICKET,
        signature=rf"{TICKET}: the note arrived as",
        oracle=oracle,
        exercised=exercised,
        fault="xml_escapes_twice",
        fault_on=fault_on,
    )
