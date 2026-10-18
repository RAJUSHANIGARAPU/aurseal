"""Locale-aware money and date parsing, so tests never compare money as strings.

Supported locales (pass ``locale=``; a :class:`NumberFormat` works too):

* ``nl_NL`` — ``1.234,56``, ``€ 1.234,56``, ``-€ 12,00``, dates ``dd-MM-yyyy``;
* ``de_DE`` — like ``nl_NL`` with ``dd.MM.yyyy`` dates;
* ``en_US`` — ``1,234.56``, ``$1,234.56``, ``-$12.00``, ``($12.00)``, dates ``MM/dd/yyyy``;
* ``en_GB`` — ``1,234.56``, dates ``dd/MM/yyyy``.

A leading minus (ASCII ``-``, Unicode ``−`` or a dash) before or after the
currency symbol is part of the amount. Credit notes and refunds print their
amounts negative; a pattern without the optional minus would read a refund of
``-€ 12,00`` as a charge of ``12,00`` and every sign check downstream would
pass for the wrong reason.

All amounts are :class:`decimal.Decimal`. ``parse_json_amount`` reads a JSON
number or numeric string (always dot-decimal) without float rounding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MINUS_CHARS = "-−–—"

# Dates are masked before amounts are scanned, so "15-03-2030" never yields 15 and -3.
_ANY_DATE = re.compile(r"\b\d{1,2}[-./]\d{1,2}[-./]\d{4}\b|\b\d{4}-\d{2}-\d{2}\b")


@dataclass(frozen=True)
class NumberFormat:
    group: str
    decimal: str
    date_format: str
    currency: str = "€$£"

    def amount_pattern(self) -> re.Pattern[str]:
        g, d = re.escape(self.group), re.escape(self.decimal)
        cur = f"[{re.escape(self.currency)}]"
        number = rf"\d{{1,3}}(?:{g}\d{{3}})+(?:{d}\d{{1,2}})?|\d+(?:{d}\d{{1,2}})?"
        minus = f"[{MINUS_CHARS}]"
        return re.compile(
            rf"(?<![\w{g}{d}])(?P<neg>{minus}\s?)?(?:{cur}\s?)?(?P<neg2>{minus}\s?)?"
            rf"(?P<num>{number})(?![\w]|{d}\d|{g}\d)"
        )


LOCALES: dict[str, NumberFormat] = {
    "nl_NL": NumberFormat(".", ",", "%d-%m-%Y"),
    "de_DE": NumberFormat(".", ",", "%d.%m.%Y"),
    "en_US": NumberFormat(",", ".", "%m/%d/%Y"),
    "en_GB": NumberFormat(",", ".", "%d/%m/%Y"),
}

_DATE_REGEX = {
    "%d-%m-%Y": r"\b\d{2}-\d{2}-\d{4}\b",
    "%d.%m.%Y": r"\b\d{2}\.\d{2}\.\d{4}\b",
    "%m/%d/%Y": r"\b\d{2}/\d{2}/\d{4}\b",
    "%d/%m/%Y": r"\b\d{2}/\d{2}/\d{4}\b",
}


def number_format(locale: str | NumberFormat) -> NumberFormat:
    if isinstance(locale, NumberFormat):
        return locale
    try:
        return LOCALES[locale]
    except KeyError:
        raise ValueError(f"unsupported locale {locale!r}; known: {sorted(LOCALES)}") from None


def parse_amount(text: str, locale: str | NumberFormat = "nl_NL") -> Decimal:
    """Parse one displayed amount; raises ``ValueError`` when ``text`` is not exactly one amount."""
    if text is None:
        raise ValueError("no amount: None")
    fmt = number_format(locale)
    raw = text.replace("\u00a0", " ").strip()
    negative = False
    if raw.startswith("(") and raw.endswith(")"):
        negative, raw = True, raw[1:-1].strip()
    m = fmt.amount_pattern().fullmatch(raw)
    if not m:
        raise ValueError(f"not a {locale} amount: {text!r}")
    return _to_decimal(m, fmt, negative)


def find_amounts(text: str, locale: str | NumberFormat = "nl_NL") -> list[Decimal]:
    """Every amount in free text, in order. Signs are kept."""
    fmt = number_format(locale)
    clean = _ANY_DATE.sub(lambda m: " " * len(m.group()), text.replace("\u00a0", " "))
    return [_to_decimal(m, fmt, False) for m in fmt.amount_pattern().finditer(clean)]


def amount_after_label(
    text: str, label: str, locale: str | NumberFormat = "nl_NL"
) -> Decimal | None:
    """The first amount on the line after the first occurrence of ``label``; None when absent."""
    for line in text.splitlines():
        at = line.find(label)
        if at >= 0:
            amounts = find_amounts(line[at + len(label) :], locale)
            if amounts:
                return amounts[0]
    return None


def parse_json_amount(value: str | int | float | None) -> Decimal | None:
    """A JSON amount (number or dot-decimal string) as Decimal, without float artefacts."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"not a JSON amount: {value!r}") from None


def parse_date(text: str, locale: str | NumberFormat = "nl_NL") -> date:
    return datetime.strptime(text.strip(), number_format(locale).date_format).date()


def find_dates(text: str, locale: str | NumberFormat = "nl_NL") -> list[date]:
    """Every valid date in the locale's format, in order; impossible dates are skipped."""
    fmt = number_format(locale)
    out = []
    for token in re.findall(_DATE_REGEX[fmt.date_format], text):
        try:
            out.append(datetime.strptime(token, fmt.date_format).date())
        except ValueError:
            continue
    return out


def date_after_label(text: str, label: str, locale: str | NumberFormat = "nl_NL") -> date | None:
    for line in text.splitlines():
        at = line.find(label)
        if at >= 0:
            dates = find_dates(line[at + len(label) :], locale)
            if dates:
                return dates[0]
    return None


def format_amount(value: Decimal, locale: str | NumberFormat = "nl_NL") -> str:
    """``Decimal('-1234.5')`` → ``-1.234,50`` (nl_NL); the inverse of :func:`parse_amount`."""
    fmt = number_format(locale)
    text = (
        f"{abs(value):,.2f}".replace(",", "\0").replace(".", fmt.decimal).replace("\0", fmt.group)
    )
    return f"-{text}" if value < 0 else text


def _to_decimal(m: re.Match[str], fmt: NumberFormat, negative: bool) -> Decimal:
    number = m.group("num").replace(fmt.group, "").replace(fmt.decimal, ".")
    value = Decimal(number)
    if negative or m.group("neg") or m.group("neg2"):
        value = -value
    return value
