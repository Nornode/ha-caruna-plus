"""Diagnostics for Caruna+."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_CUSTOMER, DATA_TOKEN, DOMAIN
from .coordinator import CarunaPlusCoordinator

TO_REDACT_ENTRY = {
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_CUSTOMER,
    DATA_TOKEN,
    "unique_id",
    "title",
}

TO_REDACT_DATA = {
    "address",
    "delivery_address",
    "streetAddress",
    "postalCode",
    "city",
    "meter_serial",
    "meterId",
    "invoiceId",
    "invoice_id",
    "amount",
    "totalAmount",
    "metering_point_id",
    "assetId",
    "ownCustomerNumbers",
    "fullName",
    "firstName",
    "lastName",
    "email",
    "phone",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: CarunaPlusCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    data_payload: dict[str, Any] = {}
    if coordinator and coordinator.data:
        d = coordinator.data
        # Use positional index instead of metering point ID as dict key to avoid
        # leaking customer-identifying numbers through async_redact_data (which
        # only redacts values, not keys).
        mp_index = {mp: f"mp_{i}" for i, mp in enumerate(sorted(
            {a.metering_point_id for a in d.assets}
            | d.energy_daily.keys()
            | d.energy_hourly.keys()
            | d.prices.keys()
        ))}
        data_payload = {
            "asset_count": len(d.assets),
            "assets": [
                {
                    "mp_index": mp_index.get(a.metering_point_id, "mp_?"),
                    "contract_type": a.contract_type,
                    "tariff_name": a.tariff_name,
                    "main_fuse_amps": a.main_fuse_amps,
                    "basic_fee_monthly": a.basic_fee_monthly,
                }
                for a in d.assets
            ],
            "energy_daily_points": {mp_index.get(mp, mp): len(s.points) for mp, s in d.energy_daily.items()},
            "energy_hourly_points": {mp_index.get(mp, mp): len(s.points) for mp, s in d.energy_hourly.items()},
            "prices": {
                mp_index.get(mp, mp): {
                    "energy_price": p.energy_price,
                    "transfer_fee": p.transfer_fee,
                    "electricity_tax": p.electricity_tax,
                    "basic_fee_monthly": p.basic_fee_monthly,
                    "vat_included": p.vat_included,
                }
                for mp, p in d.prices.items()
            },
            "billing": (
                {
                    "last_invoice_status": d.billing.last_invoice.status if d.billing.last_invoice else None,
                    "open_invoice_count": len(d.billing.open_invoices),
                    "has_overdue": d.billing.has_overdue,
                    "ytd_spend": d.billing.year_to_date_spend,
                    "next_estimate": d.billing.next_invoice_estimate,
                }
                if d.billing
                else None
            ),
            "last_success": {k: v.isoformat() for k, v in d.last_success.items()},
        }

    return {
        "entry": async_redact_data(
            {"data": entry.data, "options": dict(entry.options)}, TO_REDACT_ENTRY
        ),
        "coordinator": async_redact_data(data_payload, TO_REDACT_DATA),
    }
