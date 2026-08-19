"""Pure unit tests for binary sensor is_on functions."""

from __future__ import annotations

from datetime import date, timedelta

from custom_components.caruna_plus.binary_sensor import _invoice_due_soon, _invoice_overdue
from custom_components.caruna_plus.coordinator import CarunaPlusData
from custom_components.caruna_plus.models import BillingSnapshot, Invoice


def _open_invoice(days_until_due: int = 3, is_overdue: bool = False) -> Invoice:
    due = date.today() + timedelta(days=days_until_due)
    return Invoice(
        invoice_id="INV-1",
        amount=50.0,
        currency="EUR",
        due_date=due,
        status="overdue" if is_overdue else "open",
        is_overdue=is_overdue,
    )


# ── _invoice_overdue ─────────────────────────────────────────────────────────

def test_invoice_overdue_true_when_has_overdue():
    inv = _open_invoice(days_until_due=-5, is_overdue=True)
    data = CarunaPlusData(
        billing=BillingSnapshot(customer="12345678", open_invoices=[inv])
    )
    assert _invoice_overdue(data) is True


def test_invoice_overdue_false_when_open_but_not_overdue():
    inv = _open_invoice(days_until_due=7, is_overdue=False)
    data = CarunaPlusData(
        billing=BillingSnapshot(customer="12345678", open_invoices=[inv])
    )
    assert _invoice_overdue(data) is False


def test_invoice_overdue_false_when_no_open_invoices():
    data = CarunaPlusData(billing=BillingSnapshot(customer="12345678"))
    assert _invoice_overdue(data) is False


def test_invoice_overdue_none_when_no_billing():
    assert _invoice_overdue(CarunaPlusData()) is None


# ── _invoice_due_soon ────────────────────────────────────────────────────────

def test_invoice_due_soon_true_within_7_days():
    inv = _open_invoice(days_until_due=3)
    data = CarunaPlusData(
        billing=BillingSnapshot(customer="12345678", open_invoices=[inv])
    )
    assert _invoice_due_soon(data) is True


def test_invoice_due_soon_true_on_same_day():
    inv = _open_invoice(days_until_due=0)
    data = CarunaPlusData(
        billing=BillingSnapshot(customer="12345678", open_invoices=[inv])
    )
    assert _invoice_due_soon(data) is True


def test_invoice_due_soon_true_exactly_7_days_away():
    inv = _open_invoice(days_until_due=7)
    data = CarunaPlusData(
        billing=BillingSnapshot(customer="12345678", open_invoices=[inv])
    )
    assert _invoice_due_soon(data) is True


def test_invoice_due_soon_false_when_8_days_away():
    inv = _open_invoice(days_until_due=8)
    data = CarunaPlusData(
        billing=BillingSnapshot(customer="12345678", open_invoices=[inv])
    )
    assert _invoice_due_soon(data) is False


def test_invoice_due_soon_false_when_no_open_invoices():
    data = CarunaPlusData(billing=BillingSnapshot(customer="12345678"))
    assert _invoice_due_soon(data) is False


def test_invoice_due_soon_none_when_no_billing():
    assert _invoice_due_soon(CarunaPlusData()) is None
