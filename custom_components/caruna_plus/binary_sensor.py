"""Binary sensors for invoice status."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CarunaPlusCoordinator, CarunaPlusData


@dataclass(frozen=True, kw_only=True)
class CarunaBillingBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[CarunaPlusData], bool | None]


def _invoice_overdue(data: CarunaPlusData) -> bool | None:
    if data.billing is None:
        return None
    return data.billing.has_overdue


def _invoice_due_soon(data: CarunaPlusData) -> bool | None:
    if data.billing is None:
        return None
    today = date.today()
    horizon = today + timedelta(days=7)
    for inv in data.billing.open_invoices:
        if inv.due_date and today <= inv.due_date <= horizon:
            return True
    return False


DESCRIPTIONS: tuple[CarunaBillingBinarySensorDescription, ...] = (
    CarunaBillingBinarySensorDescription(
        key="invoice_overdue",
        translation_key="invoice_overdue",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_invoice_overdue,
    ),
    CarunaBillingBinarySensorDescription(
        key="invoice_due_soon",
        translation_key="invoice_due_soon",
        icon="mdi:calendar-alert",
        is_on_fn=_invoice_due_soon,
    ),
)


class CarunaBillingBinarySensor(CoordinatorEntity[CarunaPlusCoordinator], BinarySensorEntity):
    entity_description: CarunaBillingBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CarunaPlusCoordinator,
        description: CarunaBillingBinarySensorDescription,
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
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.coordinator.data)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CarunaPlusCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(CarunaBillingBinarySensor(coordinator, d) for d in DESCRIPTIONS)
