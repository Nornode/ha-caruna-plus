"""Caruna+ sensors — consumption, contract, cost, billing."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CURRENCY_EURO,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfEnergy,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CarunaPlusCoordinator, CarunaPlusData
from .models import Asset

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class CarunaAssetSensorDescription(SensorEntityDescription):
    """Sensor bound to a metering point."""

    value_fn: Callable[[CarunaPlusData, str], Any]


@dataclass(frozen=True, kw_only=True)
class CarunaCustomerSensorDescription(SensorEntityDescription):
    """Sensor bound to a customer (billing)."""

    value_fn: Callable[[CarunaPlusData], Any]


# --- Consumption ---------------------------------------------------------

def _energy_today(data: CarunaPlusData, mp: str) -> float | None:
    series = data.energy_hourly.get(mp) or data.energy_daily.get(mp)
    if series is None:
        return None
    today = date.today()
    return round(sum(p.kwh for p in series.points if p.timestamp.date() == today), 3) or None


def _energy_yesterday(data: CarunaPlusData, mp: str) -> float | None:
    from datetime import timedelta

    series = data.energy_daily.get(mp)
    if series is None:
        return None
    yesterday = date.today() - timedelta(days=1)
    total = sum(p.kwh for p in series.points if p.timestamp.date() == yesterday)
    return round(total, 3) or None


def _energy_month_to_date(data: CarunaPlusData, mp: str) -> float | None:
    # Prefer daily aggregates (full month rows); hourly only has today's data
    series = data.energy_daily.get(mp) or data.energy_hourly.get(mp)
    if series is None:
        return None
    today = date.today()
    total = sum(p.kwh for p in series.points if p.timestamp.date().replace(day=1) == today.replace(day=1))
    return round(total, 3) or None


def _last_reading_time(data: CarunaPlusData, mp: str) -> datetime | None:
    series = data.energy_hourly.get(mp) or data.energy_daily.get(mp)
    if series is None or not series.points:
        return None
    return max(p.timestamp for p in series.points)


# --- Contract ------------------------------------------------------------

def _asset(data: CarunaPlusData, mp: str) -> Asset | None:
    return next((a for a in data.assets if a.metering_point_id == mp), None)


def _fuse(data: CarunaPlusData, mp: str) -> int | None:
    asset = _asset(data, mp)
    return asset.main_fuse_amps if asset else None


def _contract_type(data: CarunaPlusData, mp: str) -> str | None:
    asset = _asset(data, mp)
    return asset.contract_type if asset else None


def _tariff(data: CarunaPlusData, mp: str) -> str | None:
    asset = _asset(data, mp)
    return asset.tariff_name if asset else None


def _address(data: CarunaPlusData, mp: str) -> str | None:
    asset = _asset(data, mp)
    return asset.address if asset else None


def _meter_serial(data: CarunaPlusData, mp: str) -> str | None:
    asset = _asset(data, mp)
    return asset.meter_serial if asset else None


# --- Cost ---------------------------------------------------------------

def _energy_price(data: CarunaPlusData, mp: str) -> float | None:
    plan = data.prices.get(mp)
    return plan.energy_price if plan else None


def _transfer_fee(data: CarunaPlusData, mp: str) -> float | None:
    plan = data.prices.get(mp)
    if plan:
        return plan.transfer_fee
    # Derive variable distribution fee per kWh from most recent daily aggregate
    series = data.energy_daily.get(mp)
    if series:
        for pt in reversed(series.points):
            if pt.kwh and pt.distribution_fee is not None:
                return round(pt.distribution_fee / pt.kwh, 5)
    return None


def _electricity_tax(data: CarunaPlusData, mp: str) -> float | None:
    plan = data.prices.get(mp)
    if plan:
        return plan.electricity_tax
    # Derive electricity tax per kWh from most recent daily aggregate
    series = data.energy_daily.get(mp)
    if series:
        for pt in reversed(series.points):
            if pt.kwh and pt.electricity_tax is not None:
                return round(pt.electricity_tax / pt.kwh, 5)
    return None


def _basic_fee(data: CarunaPlusData, mp: str) -> float | None:
    plan = data.prices.get(mp)
    if plan and plan.basic_fee_monthly is not None:
        return plan.basic_fee_monthly
    asset = _asset(data, mp)
    return asset.basic_fee_monthly if asset else None


def _cost_month_to_date(data: CarunaPlusData, mp: str) -> float | None:
    plan = data.prices.get(mp)
    if plan is not None and plan.total_unit_price is not None:
        kwh = _energy_month_to_date(data, mp)
        if kwh is None:
            return None
        basic = _basic_fee(data, mp) or 0.0
        today = date.today()
        day_fraction = today.day / 30.0
        return round(kwh * plan.total_unit_price + basic * day_fraction, 2)
    # No separate prices endpoint — sum totalFee from the monthly energy aggregates.
    # totalFee already includes distribution + tax + VAT per day.
    series = data.energy_daily.get(mp)
    if series is None:
        return None
    today = date.today()
    total = sum(
        p.total_fee for p in series.points
        if p.total_fee is not None
        and p.timestamp.date().replace(day=1) == today.replace(day=1)
    )
    return round(total, 2) if total else None


def _cost_projected_month(data: CarunaPlusData, mp: str) -> float | None:
    mtd = _cost_month_to_date(data, mp)
    if mtd is None:
        return None
    today = date.today()
    if today.day == 0:
        return None
    return round(mtd * 30.0 / max(today.day, 1), 2)


# --- Billing ------------------------------------------------------------

def _last_invoice_amount(data: CarunaPlusData) -> float | None:
    if data.billing and data.billing.last_invoice:
        return data.billing.last_invoice.amount
    return None


def _last_invoice_due(data: CarunaPlusData) -> datetime | None:
    if data.billing and data.billing.last_invoice and data.billing.last_invoice.due_date:
        return datetime.combine(data.billing.last_invoice.due_date, datetime.min.time(), tzinfo=timezone.utc)
    return None


def _last_invoice_status(data: CarunaPlusData) -> str | None:
    if data.billing and data.billing.last_invoice:
        return data.billing.last_invoice.status
    return None


def _next_invoice_estimate(data: CarunaPlusData) -> float | None:
    return data.billing.next_invoice_estimate if data.billing else None


def _ytd_spend(data: CarunaPlusData) -> float | None:
    return data.billing.year_to_date_spend if data.billing else None


# --- Descriptions -------------------------------------------------------

CONSUMPTION_DESCRIPTIONS: tuple[CarunaAssetSensorDescription, ...] = (
    CarunaAssetSensorDescription(
        key="energy_today",
        translation_key="energy_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_energy_today,
    ),
    CarunaAssetSensorDescription(
        key="energy_yesterday",
        translation_key="energy_yesterday",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_energy_yesterday,
    ),
    CarunaAssetSensorDescription(
        key="energy_month_to_date",
        translation_key="energy_month_to_date",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_energy_month_to_date,
    ),
    CarunaAssetSensorDescription(
        key="last_reading_time",
        translation_key="last_reading_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_reading_time,
    ),
)

CONTRACT_DESCRIPTIONS: tuple[CarunaAssetSensorDescription, ...] = (
    CarunaAssetSensorDescription(
        key="main_fuse",
        translation_key="main_fuse",
        icon="mdi:fuse",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_fuse,
    ),
    CarunaAssetSensorDescription(
        key="contract_type",
        translation_key="contract_type",
        icon="mdi:file-document-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_contract_type,
    ),
    CarunaAssetSensorDescription(
        key="tariff",
        translation_key="tariff",
        icon="mdi:cash-multiple",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_tariff,
    ),
    CarunaAssetSensorDescription(
        key="delivery_address",
        translation_key="delivery_address",
        icon="mdi:map-marker",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_address,
    ),
    CarunaAssetSensorDescription(
        key="meter_serial",
        translation_key="meter_serial",
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_meter_serial,
    ),
)

COST_DESCRIPTIONS: tuple[CarunaAssetSensorDescription, ...] = (
    CarunaAssetSensorDescription(
        key="energy_price",
        translation_key="energy_price",
        icon="mdi:currency-eur",
        native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=5,
        entity_registry_enabled_default=False,
        value_fn=_energy_price,
    ),
    CarunaAssetSensorDescription(
        key="transfer_fee",
        translation_key="transfer_fee",
        icon="mdi:transmission-tower",
        native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=5,
        value_fn=_transfer_fee,
    ),
    CarunaAssetSensorDescription(
        key="electricity_tax",
        translation_key="electricity_tax",
        icon="mdi:cash-100",
        native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=5,
        value_fn=_electricity_tax,
    ),
    CarunaAssetSensorDescription(
        key="basic_fee_monthly",
        translation_key="basic_fee_monthly",
        icon="mdi:cash-clock",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=_basic_fee,
    ),
    CarunaAssetSensorDescription(
        key="cost_month_to_date",
        translation_key="cost_month_to_date",
        icon="mdi:cash-plus",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=_cost_month_to_date,
    ),
    CarunaAssetSensorDescription(
        key="cost_projected_month",
        translation_key="cost_projected_month",
        icon="mdi:cash-fast",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=_cost_projected_month,
    ),
)

BILLING_DESCRIPTIONS: tuple[CarunaCustomerSensorDescription, ...] = (
    CarunaCustomerSensorDescription(
        key="last_invoice_amount",
        translation_key="last_invoice_amount",
        icon="mdi:file-document",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=_last_invoice_amount,
    ),
    CarunaCustomerSensorDescription(
        key="last_invoice_due_date",
        translation_key="last_invoice_due_date",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_invoice_due,
    ),
    CarunaCustomerSensorDescription(
        key="last_invoice_status",
        translation_key="last_invoice_status",
        icon="mdi:receipt-text-check",
        value_fn=_last_invoice_status,
    ),
    CarunaCustomerSensorDescription(
        key="next_invoice_estimate",
        translation_key="next_invoice_estimate",
        icon="mdi:crystal-ball",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=_next_invoice_estimate,
    ),
    CarunaCustomerSensorDescription(
        key="year_to_date_spend",
        translation_key="year_to_date_spend",
        icon="mdi:chart-line",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=_ytd_spend,
    ),
)


# --- Entities -----------------------------------------------------------

class CarunaAssetSensor(CoordinatorEntity[CarunaPlusCoordinator], SensorEntity):
    """Sensor bound to a metering-point asset."""

    entity_description: CarunaAssetSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CarunaPlusCoordinator,
        asset: Asset,
        description: CarunaAssetSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._mp = asset.metering_point_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{asset.metering_point_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, asset.metering_point_id)},
            name=asset.address or f"Caruna+ {asset.metering_point_id}",
            manufacturer="Caruna",
            model=asset.tariff_name or asset.contract_type or "Metering point",
            serial_number=asset.meter_serial,
            configuration_url="https://plus.caruna.fi",
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data, self._mp)


class CarunaCustomerSensor(CoordinatorEntity[CarunaPlusCoordinator], SensorEntity):
    """Sensor bound to the customer (billing)."""

    entity_description: CarunaCustomerSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CarunaPlusCoordinator,
        description: CarunaCustomerSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        customer = coordinator.customer or "unknown"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{customer}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"customer:{customer}")},
            name=f"Caruna+ ({customer})",
            manufacturer="Caruna",
            model="Customer account",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://plus.caruna.fi",
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CarunaPlusCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    for asset in coordinator.data.assets:
        for desc in (*CONSUMPTION_DESCRIPTIONS, *CONTRACT_DESCRIPTIONS, *COST_DESCRIPTIONS):
            entities.append(CarunaAssetSensor(coordinator, asset, desc))
    for desc in BILLING_DESCRIPTIONS:
        entities.append(CarunaCustomerSensor(coordinator, desc))

    async_add_entities(entities)
