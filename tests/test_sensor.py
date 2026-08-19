"""Pure unit tests for sensor value functions — no hass fixture needed."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from custom_components.caruna_plus.coordinator import CarunaPlusData
from custom_components.caruna_plus.models import (
    Asset,
    BillingSnapshot,
    EnergyPoint,
    EnergySeries,
    Invoice,
    PricePlan,
)
from custom_components.caruna_plus.sensor import (
    _address,
    _basic_fee,
    _contract_type,
    _cost_month_to_date,
    _cost_projected_month,
    _electricity_tax,
    _energy_month_to_date,
    _energy_price,
    _energy_today,
    _energy_yesterday,
    _fuse,
    _last_invoice_amount,
    _last_invoice_due,
    _last_invoice_status,
    _last_reading_time,
    _meter_serial,
    _next_invoice_estimate,
    _tariff,
    _transfer_fee,
    _ytd_spend,
)

MP = "MP-001"


def _pt(d: date, kwh: float, **kw) -> EnergyPoint:
    ts = datetime(d.year, d.month, d.day, tzinfo=UTC)
    return EnergyPoint(timestamp=ts, kwh=kwh, **kw)


def _series(points: list[EnergyPoint], timespan: str = "daily") -> EnergySeries:
    return EnergySeries(metering_point_id=MP, timespan=timespan, points=points)


def _asset(**kw) -> Asset:
    defaults = dict(
        metering_point_id=MP,
        customer="12345678",
        address="Testikatu 1 00100 Helsinki",
        meter_serial="MTR-9999",
        main_fuse_amps=25,
        contract_type="Yleissähkö",
        tariff_name="Yleissiirto",
        basic_fee_monthly=4.5,
    )
    defaults.update(kw)
    return Asset(**defaults)


def _invoice(status="paid", amount=100.0, days_ago=10) -> Invoice:
    d = date.today() - timedelta(days=days_ago)
    return Invoice(
        invoice_id="INV-1",
        amount=amount,
        currency="EUR",
        due_date=d,
        status=status,
        issued_date=d - timedelta(days=28),
    )


# ── Consumption ─────────────────────────────────────────────────────────────

def test_energy_today_sums_todays_points():
    today = date.today()
    data = CarunaPlusData()
    data.energy_hourly[MP] = _series([
        _pt(today, 1.5),
        _pt(today, 2.0),
        _pt(today - timedelta(days=1), 0.5),
    ])
    assert _energy_today(data, MP) == pytest.approx(3.5)


def test_energy_today_returns_none_when_no_series():
    assert _energy_today(CarunaPlusData(), MP) is None


def test_energy_today_returns_none_when_all_points_are_yesterday():
    yesterday = date.today() - timedelta(days=1)
    data = CarunaPlusData()
    data.energy_hourly[MP] = _series([_pt(yesterday, 5.0)])
    assert _energy_today(data, MP) is None


def test_energy_yesterday_sums_daily_points():
    today = date.today()
    yesterday = today - timedelta(days=1)
    data = CarunaPlusData()
    data.energy_daily[MP] = _series([
        _pt(yesterday, 10.5),
        _pt(today, 3.0),
    ])
    assert _energy_yesterday(data, MP) == pytest.approx(10.5)


def test_energy_yesterday_returns_none_when_no_series():
    assert _energy_yesterday(CarunaPlusData(), MP) is None


def test_energy_month_to_date_sums_current_month():
    today = date.today()
    last_month_end = today.replace(day=1) - timedelta(days=1)
    data = CarunaPlusData()
    data.energy_daily[MP] = _series([
        _pt(today, 5.0),
        _pt(today.replace(day=1), 3.0),
        _pt(last_month_end, 20.0),
    ])
    assert _energy_month_to_date(data, MP) == pytest.approx(8.0)


def test_last_reading_time_returns_latest_timestamp():
    today = date.today()
    yesterday = today - timedelta(days=1)
    ts_today = datetime(today.year, today.month, today.day, tzinfo=UTC)
    ts_yest = datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=UTC)
    data = CarunaPlusData()
    data.energy_hourly[MP] = _series([
        EnergyPoint(timestamp=ts_yest, kwh=1.0),
        EnergyPoint(timestamp=ts_today, kwh=2.0),
    ])
    assert _last_reading_time(data, MP) == ts_today


def test_last_reading_time_returns_none_when_empty_series():
    data = CarunaPlusData()
    data.energy_hourly[MP] = _series([])
    assert _last_reading_time(data, MP) is None


# ── Contract ────────────────────────────────────────────────────────────────

def test_fuse_returns_amps():
    data = CarunaPlusData(assets=[_asset(main_fuse_amps=25)])
    assert _fuse(data, MP) == 25


def test_contract_type():
    data = CarunaPlusData(assets=[_asset(contract_type="Yleissähkö")])
    assert _contract_type(data, MP) == "Yleissähkö"


def test_tariff():
    data = CarunaPlusData(assets=[_asset(tariff_name="Yleissiirto")])
    assert _tariff(data, MP) == "Yleissiirto"


def test_address():
    data = CarunaPlusData(assets=[_asset(address="Testikatu 1")])
    assert _address(data, MP) == "Testikatu 1"


def test_meter_serial():
    data = CarunaPlusData(assets=[_asset(meter_serial="MTR-1234")])
    assert _meter_serial(data, MP) == "MTR-1234"


def test_contract_sensors_return_none_when_no_matching_asset():
    data = CarunaPlusData(assets=[_asset(metering_point_id="OTHER")])
    assert _fuse(data, MP) is None
    assert _contract_type(data, MP) is None
    assert _address(data, MP) is None


# ── Cost ────────────────────────────────────────────────────────────────────

def test_energy_price_from_price_plan():
    data = CarunaPlusData()
    data.prices[MP] = PricePlan(energy_price=0.08)
    assert _energy_price(data, MP) == pytest.approx(0.08)


def test_energy_price_none_when_no_plan():
    assert _energy_price(CarunaPlusData(), MP) is None


def test_transfer_fee_derived_from_daily_energy():
    today = date.today()
    data = CarunaPlusData()
    data.energy_daily[MP] = _series([_pt(today, kwh=2.0, distribution_fee=0.10)])
    assert _transfer_fee(data, MP) == pytest.approx(0.05)


def test_electricity_tax_derived_from_daily_energy():
    today = date.today()
    data = CarunaPlusData()
    data.energy_daily[MP] = _series([_pt(today, kwh=4.0, electricity_tax=0.20)])
    assert _electricity_tax(data, MP) == pytest.approx(0.05)


def test_basic_fee_from_asset():
    data = CarunaPlusData(assets=[_asset(basic_fee_monthly=4.5)])
    assert _basic_fee(data, MP) == pytest.approx(4.5)


def test_cost_month_to_date_sums_total_fee():
    today = date.today()
    data = CarunaPlusData()
    data.energy_daily[MP] = _series([
        _pt(today, kwh=5.0, total_fee=1.50),
        _pt(today.replace(day=1), kwh=3.0, total_fee=0.90),
        _pt(today.replace(day=1) - timedelta(days=1), kwh=2.0, total_fee=9.99),
    ])
    assert _cost_month_to_date(data, MP) == pytest.approx(1.50 + 0.90)


def test_cost_projected_month_scales_up():
    today = date.today()
    data = CarunaPlusData()
    data.energy_daily[MP] = _series([_pt(today, kwh=5.0, total_fee=10.0)])
    mtd = _cost_month_to_date(data, MP)
    projected = _cost_projected_month(data, MP)
    assert projected is not None
    assert projected == pytest.approx(round(mtd * 30.0 / max(today.day, 1), 2))


def test_cost_month_to_date_none_when_no_series():
    assert _cost_month_to_date(CarunaPlusData(), MP) is None


# ── Billing ─────────────────────────────────────────────────────────────────

def test_last_invoice_amount():
    inv = _invoice(amount=120.50)
    data = CarunaPlusData(billing=BillingSnapshot(customer="12345678", last_invoice=inv))
    assert _last_invoice_amount(data) == pytest.approx(120.50)


def test_last_invoice_amount_none_when_no_billing():
    assert _last_invoice_amount(CarunaPlusData()) is None


def test_last_invoice_status():
    inv = _invoice(status="paid")
    data = CarunaPlusData(billing=BillingSnapshot(customer="12345678", last_invoice=inv))
    assert _last_invoice_status(data) == "paid"


def test_last_invoice_due_returns_datetime():
    d = date.today() - timedelta(days=5)
    inv = Invoice(invoice_id="X", amount=50.0, currency="EUR", due_date=d, status="paid")
    data = CarunaPlusData(billing=BillingSnapshot(customer="12345678", last_invoice=inv))
    result = _last_invoice_due(data)
    assert result is not None
    assert result.date() == d


def test_last_invoice_due_none_when_no_due_date():
    inv = Invoice(invoice_id="X", amount=50.0, currency="EUR", due_date=None, status="paid")
    data = CarunaPlusData(billing=BillingSnapshot(customer="12345678", last_invoice=inv))
    assert _last_invoice_due(data) is None


def test_ytd_spend():
    data = CarunaPlusData(billing=BillingSnapshot(customer="12345678", year_to_date_spend=500.0))
    assert _ytd_spend(data) == pytest.approx(500.0)


def test_ytd_spend_none_when_no_billing():
    assert _ytd_spend(CarunaPlusData()) is None


def test_next_invoice_estimate():
    data = CarunaPlusData(billing=BillingSnapshot(customer="12345678", next_invoice_estimate=80.0))
    assert _next_invoice_estimate(data) == pytest.approx(80.0)
