"""Typed data models returned by the Caruna+ client."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(slots=True)
class TokenStore:
    """Bearer token + refresh state, persisted across HA restarts."""

    access_token: str | None = None
    expires_at: datetime | None = None
    customer_numbers: list[str] = field(default_factory=list)
    mfa_trust_cookie: str | None = None

    def is_expired(self, margin_seconds: int = 0) -> bool:
        if not self.access_token or not self.expires_at:
            return True
        return (self.expires_at.timestamp() - margin_seconds) <= datetime.now().timestamp()

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "customer_numbers": self.customer_numbers,
            "mfa_trust_cookie": self.mfa_trust_cookie,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TokenStore:
        if not data:
            return cls()
        expires_raw = data.get("expires_at")
        expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None
        return cls(
            access_token=data.get("access_token"),
            expires_at=expires_at,
            customer_numbers=list(data.get("customer_numbers") or []),
            mfa_trust_cookie=data.get("mfa_trust_cookie"),
        )


@dataclass(slots=True)
class Customer:
    number: str
    name: str | None = None


@dataclass(slots=True)
class Asset:
    """A metering point plus embedded contract metadata."""

    metering_point_id: str
    customer: str
    address: str | None = None
    meter_serial: str | None = None
    main_fuse_amps: int | None = None
    contract_type: str | None = None
    tariff_name: str | None = None
    basic_fee_monthly: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EnergyPoint:
    timestamp: datetime
    kwh: float
    # Cost breakdown fields — present when the API returns them (EUR per period, VAT included)
    total_fee: float | None = None
    distribution_fee: float | None = None
    distribution_base_fee: float | None = None
    electricity_tax: float | None = None
    vat: float | None = None
    temperature: float | None = None


@dataclass(slots=True)
class EnergySeries:
    metering_point_id: str
    timespan: str  # "hourly" | "daily"
    points: list[EnergyPoint] = field(default_factory=list)

    @property
    def latest(self) -> EnergyPoint | None:
        return self.points[-1] if self.points else None

    @property
    def total_kwh(self) -> float:
        return sum(p.kwh for p in self.points)


@dataclass(slots=True)
class PricePlan:
    """Current pricing for a metering point. Values in EUR/kWh (not cents)."""

    energy_price: float | None = None
    transfer_fee: float | None = None
    electricity_tax: float | None = None
    basic_fee_monthly: float | None = None
    vat_included: bool = True

    @property
    def total_unit_price(self) -> float | None:
        parts = [self.energy_price, self.transfer_fee, self.electricity_tax]
        if any(p is None for p in parts):
            return None
        return sum(p for p in parts if p is not None)


@dataclass(slots=True)
class Invoice:
    invoice_id: str
    amount: float
    currency: str
    due_date: date | None
    status: str  # "paid" | "open" | "overdue" | "unknown"
    issued_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    amount_open: float | None = None  # outstanding balance (from amountOpen)
    is_overdue: bool = False  # from isOverdue field


@dataclass(slots=True)
class InvoiceDetail(Invoice):
    energy_kwh: float | None = None
    energy_cost: float | None = None
    transfer_cost: float | None = None
    tax_cost: float | None = None
    basic_fee: float | None = None
    line_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class BillingSnapshot:
    """Aggregate billing view for one customer."""

    customer: str
    last_invoice: Invoice | None = None
    next_invoice_estimate: float | None = None
    year_to_date_spend: float | None = None
    open_invoices: list[Invoice] = field(default_factory=list)

    @property
    def has_overdue(self) -> bool:
        return any(inv.is_overdue or inv.status == "overdue" for inv in self.open_invoices)
