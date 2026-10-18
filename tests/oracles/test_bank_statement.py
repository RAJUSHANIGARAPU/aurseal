"""Tests for oracles.bank_statement (invented camt.053 documents)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from oracles.bank_statement import (
    CREDIT,
    DEBIT,
    StatementEntry,
    StatementFormatError,
    parse_camt053,
)

STATEMENT = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
  <BkToCstmrStmt>
    <GrpHdr><MsgId>STMT-1</MsgId></GrpHdr>
    <Stmt>
      <Id>STMT-1-1</Id>
      <Ntry>
        <Amt Ccy="EUR">125.40</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <BookgDt><Dt>2031-05-04</Dt></BookgDt>
        <NtryDtls><TxDtls>
          <Refs><EndToEndId>INV-2031-0042</EndToEndId></Refs>
          <RltdPties>
            <Dbtr><Pty><Nm>Example Trading Ltd</Nm></Pty></Dbtr>
            <Cdtr><Pty><Nm>Sample Shop</Nm></Pty></Cdtr>
          </RltdPties>
          <RmtInf><Ustrd>Invoice INV-2031-0042</Ustrd><Ustrd>order ORD-42</Ustrd></RmtInf>
        </TxDtls></NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="EUR">20</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <BookgDt><DtTm>2031-05-05T09:30:00+02:00</DtTm></BookgDt>
        <NtryDtls><TxDtls>
          <RltdPties>
            <Dbtr><Nm>Sample Shop</Nm></Dbtr>
            <Cdtr><Nm>Refund Recipient</Nm></Cdtr>
          </RltdPties>
        </TxDtls></NtryDtls>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>"""


def test_entries_in_document_order():
    credit, debit = parse_camt053(STATEMENT)
    assert credit == StatementEntry(
        Decimal("125.40"),
        "EUR",
        CREDIT,
        date(2031, 5, 4),
        "INV-2031-0042",
        "Example Trading Ltd",
        "Invoice INV-2031-0042 order ORD-42",
    )
    assert debit.direction == DEBIT and debit.counterparty == "Refund Recipient"
    assert debit.booking_date == date(2031, 5, 5)
    assert debit.reference is None and debit.remittance is None
    assert credit.signed_amount == Decimal("125.40") and debit.signed_amount == Decimal("-20")


def test_prefixed_namespace_and_bytes_input():
    prefixed = STATEMENT.replace("<Document xmlns=", "<c:Document xmlns:c=").replace(
        "</Document>", "</c:Document>"
    )
    # only the root is prefixed; children are then un-namespaced: still parsed by local name
    assert len(parse_camt053(prefixed.encode())) == 2


def test_statement_without_entries():
    xml = "<Document><BkToCstmrStmt><Stmt><Id>x</Id></Stmt></BkToCstmrStmt></Document>"
    assert parse_camt053(xml) == []


@pytest.mark.parametrize(
    "xml, message",
    [
        ("<Document/>", "no Stmt"),
        ("<Document><Stmt>", "not well-formed"),
        ("<Document><Stmt><Ntry><CdtDbtInd>CRDT</CdtDbtInd></Ntry></Stmt></Document>", "Amt"),
        (
            "<Document><Stmt><Ntry><Amt>1,00</Amt><CdtDbtInd>CRDT</CdtDbtInd></Ntry></Stmt>"
            "</Document>",
            "not a number",
        ),
        (
            "<Document><Stmt><Ntry><Amt>1</Amt><CdtDbtInd>X</CdtDbtInd></Ntry></Stmt></Document>",
            "CdtDbtInd",
        ),
        ('<!DOCTYPE d [<!ENTITY a "x">]><Document><Stmt/></Document>', "DOCTYPE"),
    ],
)
def test_malformed_statements(xml, message):
    with pytest.raises(StatementFormatError, match=message):
        parse_camt053(xml)
