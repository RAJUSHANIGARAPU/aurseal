"""The demo partner message's completeness rules, built from the generic rule runner.

The invented segment syntax is ``HDR`` (id, type), ``SUB`` (number, version), ``CUS``
(customer number, type code, display name), ``AMT`` (qualifier, amount) for NET, TAX, FEE and
TOT, one ``NTE`` per invoice note, and an ``END`` trailer counting the segments.
"""

from __future__ import annotations

from oracles import message_rules as mr

#: The type codes the receiver expects per customer type (the receiver's own table).
CUSTOMER_TYPE_CODES = {"INDIVIDUAL": "I1", "COMPANY": "C1"}


def _amount(qualifier: str) -> mr.Value:
    return mr.segment_value("AMT", 1, qualifier=qualifier)


def demo_rules(customer_type: str) -> list[mr.Rule]:
    return [
        mr.required_group("header", "HDR", reason="the receiver routes on the header"),
        mr.required_group("subscription", "SUB", reason="the receiver files by subscription"),
        mr.required_group("customer", "CUS", reason="the receiver bills the customer"),
        mr.totals_balance(
            "invoice total",
            _amount("TOT"),
            [_amount("NET"), _amount("TAX"), _amount("FEE")],
            reason="the receiver rejects a total that is not the sum of its parts",
        ),
        mr.code_mapped(
            "customer type code",
            mr.segment_value("CUS", 1),
            customer_type,
            CUSTOMER_TYPE_CODES,
            reason="the receiver only knows its own type codes",
        ),
        mr.declared_count("segment count", "END", reason="a truncated message must be detectable"),
        mr.xml_well_formed(
            "document data", lambda p: p.xml, reason="the document part is printed by the receiver"
        ),
    ]
