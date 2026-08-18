"""API-parsing tests. Auth is short-circuited via a pre-populated TokenStore."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.caruna_plus.api import CarunaPlusClient
from custom_components.caruna_plus.const import EP_ASSETS, EP_ENERGY, EP_INVOICES
from custom_components.caruna_plus.models import TokenStore


def _fresh_store() -> TokenStore:
    return TokenStore(
        access_token="fake-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        customer_numbers=["12345678"],
    )


@pytest.mark.asyncio
async def test_get_assets_parses_all_fields(assets_response_json) -> None:
    with aioresponses() as mocked:
        mocked.get(EP_ASSETS.format(customer="12345678"), payload=assets_response_json)

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            assets = await client.async_get_assets("12345678")

        assert len(assets) == 1
        asset = assets[0]
        assert asset.metering_point_id == "MP-1"
        assert asset.meter_serial == "MTR-9999"
        assert asset.main_fuse_amps == 25  # "3x25A" → split on "x" → "25A" → 25
        assert asset.contract_type == "Yleissähkö"
        assert asset.tariff_name == "Yleissiirto"
        assert asset.basic_fee_monthly == 4.5
        assert asset.address is not None
        assert "Testikatu 1" in asset.address


@pytest.mark.asyncio
async def test_get_energy_parses_points(energy_response_json) -> None:
    target = date(2026, 8, 12)
    with aioresponses() as mocked:
        mocked.get(
            re.compile(re.escape(EP_ENERGY.format(customer="12345678", mp="MP-1")) + r"\?.*"),
            payload=energy_response_json,
        )

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            series = await client.async_get_energy("12345678", "MP-1", target, "hourly")

        assert series.timespan == "hourly"
        assert len(series.points) == 2
        assert series.points[0].kwh == pytest.approx(1.234)
        assert series.total_kwh == pytest.approx(1.234 + 0.987)


@pytest.mark.asyncio
async def test_get_billing_derives_open_and_ytd(
    invoices_open_response, invoices_paid_response
) -> None:
    with aioresponses() as mocked:
        mocked.get(
            EP_INVOICES.format(customer="12345678"),
            params={"status": "open"},
            payload=invoices_open_response,
        )
        mocked.get(
            EP_INVOICES.format(customer="12345678"),
            params={"status": "paid"},
            payload=invoices_paid_response,
        )

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            snapshot = await client.async_get_billing("12345678")

        assert snapshot.customer == "12345678"
        assert snapshot.last_invoice is not None
        assert snapshot.last_invoice.invoice_id == "INV-OPEN"  # most recent (Aug > Jul)
        assert snapshot.last_invoice.status == "open"
        assert len(snapshot.open_invoices) == 1
        assert snapshot.year_to_date_spend == pytest.approx(120.5 + 95.0)


@pytest.mark.asyncio
async def test_get_assets_wrapped_in_dict(assets_response_json) -> None:
    """Assets returned as {"assets": [...]} should be unwrapped."""
    with aioresponses() as mocked:
        mocked.get(EP_ASSETS.format(customer="12345678"), payload={"assets": assets_response_json})

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            assets = await client.async_get_assets("12345678")

        assert len(assets) == 1
        assert assets[0].metering_point_id == "MP-1"


@pytest.mark.asyncio
async def test_get_assets_empty_returns_empty_list() -> None:
    with aioresponses() as mocked:
        mocked.get(EP_ASSETS.format(customer="12345678"), payload=[])

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            assets = await client.async_get_assets("12345678")

        assert assets == []


@pytest.mark.asyncio
async def test_get_energy_uses_totalconsumption_primary(energy_response_with_costs) -> None:
    """totalConsumption takes priority over the legacy 'value' field."""
    target = date(2026, 8, 12)
    with aioresponses() as mocked:
        mocked.get(
            re.compile(re.escape(EP_ENERGY.format(customer="12345678", mp="MP-1")) + r"\?.*"),
            payload=energy_response_with_costs,
        )

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            series = await client.async_get_energy("12345678", "MP-1", target, "hourly")

        assert len(series.points) == 2
        assert series.points[0].kwh == pytest.approx(2.5)
        assert series.total_kwh == pytest.approx(2.5 + 1.8)


@pytest.mark.asyncio
async def test_get_energy_populates_cost_fields(energy_response_with_costs) -> None:
    """Cost fields (totalFee, distributionFee, etc.) are parsed into EnergyPoint."""
    target = date(2026, 8, 12)
    with aioresponses() as mocked:
        mocked.get(
            re.compile(re.escape(EP_ENERGY.format(customer="12345678", mp="MP-1")) + r"\?.*"),
            payload=energy_response_with_costs,
        )

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            series = await client.async_get_energy("12345678", "MP-1", target, "hourly")

        pt = series.points[0]
        assert pt.total_fee == pytest.approx(0.80)
        assert pt.distribution_fee == pytest.approx(0.30)
        assert pt.electricity_tax == pytest.approx(0.10)
        assert pt.vat == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_get_energy_skips_entries_with_null_kwh() -> None:
    """Entries with no consumption field are silently dropped."""
    target = date(2026, 8, 12)
    payload = {
        "values": [
            {"timestamp": "2026-08-12T00:00:00+00:00"},  # no kwh field at all
            {"timestamp": "2026-08-12T01:00:00+00:00", "totalConsumption": 3.0},
        ]
    }
    with aioresponses() as mocked:
        mocked.get(
            re.compile(re.escape(EP_ENERGY.format(customer="12345678", mp="MP-1")) + r"\?.*"),
            payload=payload,
        )

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            series = await client.async_get_energy("12345678", "MP-1", target, "hourly")

        assert len(series.points) == 1
        assert series.points[0].kwh == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_get_energy_monthly_omits_day_param(energy_response_json) -> None:
    """Monthly requests must not include a day= query param."""
    target = date(2026, 8, 1)
    with aioresponses() as mocked:
        mocked.get(
            re.compile(re.escape(EP_ENERGY.format(customer="12345678", mp="MP-1")) + r"\?.*"),
            payload=energy_response_json,
        )

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            await client.async_get_energy("12345678", "MP-1", target, "monthly")

    called_urls = [str(url) for _, url in mocked.requests.keys()]
    assert len(called_urls) == 1
    assert "day=" not in called_urls[0]
    assert "timespan=monthly" in called_urls[0]


@pytest.mark.asyncio
async def test_get_invoices_fetches_open_and_paid_separately(
    invoices_open_response, invoices_paid_response
) -> None:
    """async_get_invoices makes two requests: one for open, one for paid."""
    with aioresponses() as mocked:
        mocked.get(
            EP_INVOICES.format(customer="12345678"),
            params={"status": "open"},
            payload=invoices_open_response,
        )
        mocked.get(
            EP_INVOICES.format(customer="12345678"),
            params={"status": "paid"},
            payload=invoices_paid_response,
        )

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            invoices = await client.async_get_invoices("12345678")

    assert len(invoices) == 2
    assert {inv.invoice_id for inv in invoices} == {"INV-OPEN", "INV-PAID"}


@pytest.mark.asyncio
async def test_429_raises_rate_limit_error() -> None:
    from custom_components.caruna_plus.auth import CarunaRateLimitError

    with aioresponses() as mocked:
        mocked.get(
            EP_ASSETS.format(customer="12345678"),
            status=429,
            headers={"Retry-After": "30"},
        )

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            with pytest.raises(CarunaRateLimitError) as excinfo:
                await client.async_get_assets("12345678")

        assert excinfo.value.retry_after == pytest.approx(30)


@pytest.mark.asyncio
async def test_500_raises_connection_error() -> None:
    from custom_components.caruna_plus.auth import CarunaConnectionError

    with aioresponses() as mocked:
        mocked.get(EP_ASSETS.format(customer="12345678"), status=503)

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            with pytest.raises(CarunaConnectionError):
                await client.async_get_assets("12345678")


@pytest.mark.asyncio
async def test_aiohttp_error_raises_connection_error() -> None:
    """A network-level drop (ClientConnectionError) wraps as CarunaConnectionError."""
    from custom_components.caruna_plus.auth import CarunaConnectionError

    with aioresponses() as mocked:
        mocked.get(
            EP_ASSETS.format(customer="12345678"),
            exception=aiohttp.ClientConnectionError("refused"),
        )

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            with pytest.raises(CarunaConnectionError):
                await client.async_get_assets("12345678")


@pytest.mark.asyncio
async def test_401_triggers_single_retry_then_gives_up(assets_response_json) -> None:
    """First call returns 401; auth invalidated and retried. Second 401 → CarunaAuthError."""
    from custom_components.caruna_plus.api import CarunaAuthError

    with aioresponses() as mocked:
        mocked.get(EP_ASSETS.format(customer="12345678"), status=401)
        mocked.get(EP_ASSETS.format(customer="12345678"), status=401)

        async with aiohttp.ClientSession() as session:
            client = CarunaPlusClient(session, "u", "p", token_store=_fresh_store())
            # Prevent the auto-retry from calling async_login and hitting the real IDP:
            async def _no_relogin(*args, **kwargs):
                raise CarunaAuthError("relogin blocked in test")

            client.auth.async_login = _no_relogin  # type: ignore[assignment]
            with pytest.raises(CarunaAuthError):
                await client.async_get_assets("12345678")
