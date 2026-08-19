"""High-level Caruna+ API client. Wraps auth + typed endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import aiohttp

from .auth import (
    CarunaAPIError,
    CarunaAuthenticator,
    CarunaAuthError,
    CarunaConnectionError,
    CarunaRateLimitError,
)
from .const import (
    EP_ASSETS,
    EP_ENERGY,
    EP_INVOICE,
    EP_INVOICES,
    EP_PRICES,
    REQUEST_TIMEOUT_SECONDS,
)
from .models import (
    Asset,
    BillingSnapshot,
    Customer,
    EnergyPoint,
    EnergySeries,
    Invoice,
    InvoiceDetail,
    PricePlan,
    TokenStore,
)

_LOGGER = logging.getLogger(__name__)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _b62(s: str) -> int:
    """Decode a base-62 string (0-9 A-Z a-z) to an integer."""
    n = 0
    for c in s:
        if "0" <= c <= "9":
            n = n * 62 + ord(c) - 48
        elif "A" <= c <= "Z":
            n = n * 62 + ord(c) - 55  # A=65, 65-55=10
        elif "a" <= c <= "z":
            n = n * 62 + ord(c) - 61  # a=97, 97-61=36
    return n


def _decompress_invoices(raw: Any, status_hint: str) -> list[dict[str, Any]]:
    """Decode the compressedInvoices columnar format returned by plus.caruna.fi.

    The format uses a flat string pool where each entry is either a literal
    string or a typed prefix:
      n|<b62_int>.<b62_hundredths>  →  float  (e.g. n|a.1Q = 36.88)
      b|T / b|F                     →  True / False
      a|idx|idx|...                 →  list of pool[idx] items
      o|schema_idx|v0|v1|...        →  dict zipped from schema + values

    The status field contains an internal numeric code; we use status_hint
    (the ?status= query parameter) as the authoritative status string instead.
    """
    if not isinstance(raw, dict):
        return []
    data = raw.get("compressedInvoices")
    if not isinstance(data, list) or len(data) < 2:
        return []
    pool_raw, root_key = data[0], data[1]
    if not isinstance(pool_raw, list):
        return []

    resolved: dict[int, Any] = {}

    def resolve(idx: int) -> Any:
        if idx in resolved:
            return resolved[idx]
        if idx >= len(pool_raw):
            return None
        val = pool_raw[idx]
        if not isinstance(val, str):
            result: Any = val
        elif val.startswith("n|"):
            inner = val[2:]
            if "." in inner:
                int_s, frac_s = inner.split(".", 1)
                # frac encodes hundredths of the currency unit
                frac = _b62(frac_s)
                result = float(_b62(int_s)) + (frac / 100.0 if frac < 100 else 0.0)
            else:
                result = float(_b62(inner))
        elif val.startswith("b|"):
            result = val[2:] == "T"
        elif val.startswith("a|"):
            parts = [p for p in val[2:].split("|") if p]
            result = [resolve(_b62(p)) for p in parts]
        elif val.startswith("o|"):
            parts = [p for p in val[2:].split("|") if p]
            if not parts:
                result = {}
            else:
                schema = resolve(_b62(parts[0]))
                values = [resolve(_b62(p)) for p in parts[1:]]
                result = dict(zip(schema, values)) if isinstance(schema, list) else {}
        else:
            result = val
        resolved[idx] = result
        return result

    root = resolve(_b62(str(root_key)))
    invoices: list[dict[str, Any]] = []
    for item in (root if isinstance(root, list) else []):
        if isinstance(item, dict):
            # Override the internal numeric status code with the query-param value.
            item["status"] = status_hint
            invoices.append(item)
    return invoices


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _to_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class CarunaPlusClient:
    """High-level typed client. All methods refresh the token as needed."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        token_store: TokenStore | None = None,
        loop_executor: Any = None,
    ) -> None:
        self._session = session
        self._auth = CarunaAuthenticator(
            session=session,
            username=username,
            password=password,
            token_store=token_store,
            loop_executor=loop_executor,
        )

    @property
    def auth(self) -> CarunaAuthenticator:
        return self._auth

    @property
    def token_store(self) -> TokenStore:
        return self._auth.token_store

    async def async_login(self, mfa_code: str | None = None) -> None:
        """Force a login. Used by config flow to validate credentials."""
        await self._auth.async_login(mfa_code=mfa_code)

    async def async_get_customers(self) -> list[Customer]:
        await self._auth.async_ensure_token()
        info = self._auth.user_info or {}
        numbers = info.get("ownCustomerNumbers") or self.token_store.customer_numbers
        name = info.get("fullName") or info.get("firstName")
        return [Customer(number=str(n), name=name) for n in numbers]

    async def async_get_assets(self, customer: str) -> list[Asset]:
        raw = await self._get_json(EP_ASSETS.format(customer=customer))
        assets_raw = raw if isinstance(raw, list) else raw.get("assets", [])
        assets: list[Asset] = []
        for item in assets_raw:
            assets.append(
                Asset(
                    metering_point_id=str(item.get("assetId") or item.get("meteringPointId") or item.get("id")),
                    customer=customer,
                    address=self._extract_address(item),
                    meter_serial=(
                        item.get("currentCounterSerialNumber")
                        or item.get("meterId")
                        or item.get("meterSerial")
                    ),
                    main_fuse_amps=self._extract_fuse(item),
                    contract_type=item.get("contractType") or item.get("productType"),
                    tariff_name=(
                        item.get("contractProductDesc")
                        or item.get("tariff")
                        or item.get("tariffName")
                    ),
                    basic_fee_monthly=_to_float(item.get("basicFee")),
                    raw=item,
                )
            )
        return assets

    async def async_get_energy(
        self,
        customer: str,
        metering_point: str,
        target_date: date,
        timespan: str = "daily",
    ) -> EnergySeries:
        # The API uses timespan=daily (24 hourly rows), timespan=monthly (daily rows).
        # "hourly" is not a valid value and returns 400 — map it to "daily".
        api_timespan = "monthly" if timespan == "monthly" else "daily"
        params: dict[str, str] = {
            "year": str(target_date.year),
            "month": str(target_date.month),
            "timespan": api_timespan,
        }
        if api_timespan != "monthly":
            params["day"] = str(target_date.day)
        raw = await self._get_json(
            EP_ENERGY.format(customer=customer, mp=metering_point),
            params=params,
        )
        points: list[EnergyPoint] = []
        for entry in raw if isinstance(raw, list) else raw.get("values", []):
            ts = _to_datetime(entry.get("timestamp") or entry.get("time"))
            kwh = _to_float(
                entry.get("totalConsumption")
                or entry.get("invoicedConsumption")
                or entry.get("value")
                or entry.get("kwh")
                or entry.get("energyConsumption")
            )
            if ts is None or kwh is None:
                continue
            points.append(EnergyPoint(
                timestamp=ts,
                kwh=kwh,
                total_fee=_to_float(entry.get("totalFee")),
                distribution_fee=_to_float(entry.get("distributionFee")),
                distribution_base_fee=_to_float(entry.get("distributionBaseFee")),
                electricity_tax=_to_float(entry.get("electricityTax")),
                vat=_to_float(entry.get("valueAddedTax")),
                temperature=_to_float(entry.get("temperature")),
            ))
        return EnergySeries(metering_point_id=metering_point, timespan=timespan, points=points)

    async def async_get_invoices(self, customer: str) -> list[Invoice]:
        # The endpoint requires ?status= — fetch open and paid separately then merge.
        items: list[dict[str, Any]] = []
        for status in ("open", "paid"):
            raw = await self._get_json(
                EP_INVOICES.format(customer=customer),
                params={"status": status},
                allow_missing=True,
            )
            if not raw:
                continue
            if isinstance(raw, dict) and "compressedInvoices" in raw:
                batch = _decompress_invoices(raw, status)
            elif isinstance(raw, list):
                batch = raw
            else:
                batch = raw.get("invoices", [])
            items.extend(batch)
        return [self._parse_invoice(item) for item in items]

    async def async_get_invoice(self, customer: str, invoice_id: str) -> InvoiceDetail | None:
        # TODO(har): endpoint path is placeholder.
        raw = await self._get_json(
            EP_INVOICE.format(customer=customer, invoice_id=invoice_id),
            allow_missing=True,
        )
        if not raw:
            return None
        base = self._parse_invoice(raw)
        return InvoiceDetail(
            invoice_id=base.invoice_id,
            amount=base.amount,
            currency=base.currency,
            due_date=base.due_date,
            status=base.status,
            issued_date=base.issued_date,
            period_start=base.period_start,
            period_end=base.period_end,
            energy_kwh=_to_float(raw.get("energyKwh")),
            energy_cost=_to_float(raw.get("energyCost")),
            transfer_cost=_to_float(raw.get("transferCost")),
            tax_cost=_to_float(raw.get("taxCost")),
            basic_fee=_to_float(raw.get("basicFee")),
            line_items=raw.get("lineItems", []),
        )

    async def async_get_prices(self, customer: str, metering_point: str) -> PricePlan:
        # TODO(har): endpoint is placeholder; if it 404s, we fall back to
        # deriving from the most recent invoice.
        raw = await self._get_json(
            EP_PRICES.format(customer=customer, mp=metering_point),
            allow_missing=True,
        )
        if raw:
            return PricePlan(
                energy_price=_to_float(raw.get("energyPrice")),
                transfer_fee=_to_float(raw.get("transferFee")),
                electricity_tax=_to_float(raw.get("electricityTax")),
                basic_fee_monthly=_to_float(raw.get("basicFee")),
                vat_included=bool(raw.get("vatIncluded", True)),
            )
        return await self._derive_price_from_invoices(customer)

    async def async_get_billing(self, customer: str) -> BillingSnapshot:
        invoices = await self.async_get_invoices(customer)
        invoices.sort(key=lambda i: i.issued_date or i.due_date or date.min, reverse=True)
        last = invoices[0] if invoices else None
        open_invoices = [i for i in invoices if i.status in {"open", "overdue"}]

        today = date.today()
        ytd = sum(i.amount for i in invoices if i.issued_date and i.issued_date.year == today.year)

        next_estimate: float | None = None
        recent = [i for i in invoices if i.issued_date and (today - i.issued_date).days <= 90]
        if len(recent) >= 2:
            next_estimate = sum(i.amount for i in recent) / len(recent)

        return BillingSnapshot(
            customer=customer,
            last_invoice=last,
            next_invoice_estimate=next_estimate,
            year_to_date_spend=ytd or None,
            open_invoices=open_invoices,
        )

    async def _derive_price_from_invoices(self, customer: str) -> PricePlan:
        invoices = await self.async_get_invoices(customer)
        for inv in sorted(invoices, key=lambda i: i.issued_date or date.min, reverse=True):
            detail = await self.async_get_invoice(customer, inv.invoice_id)
            if not detail or not detail.energy_kwh:
                continue
            def unit(cost: float | None, _kwh: float = detail.energy_kwh) -> float | None:
                return (cost / _kwh) if cost and _kwh else None

            return PricePlan(
                energy_price=unit(detail.energy_cost),
                transfer_fee=unit(detail.transfer_cost),
                electricity_tax=unit(detail.tax_cost),
                basic_fee_monthly=detail.basic_fee,
                vat_included=True,
            )
        return PricePlan()

    def _parse_invoice(self, raw: dict[str, Any]) -> Invoice:
        due = _to_date(raw.get("dueDate") or raw.get("due"))
        is_overdue = bool(raw.get("isOverdue", False))
        status = str(raw.get("status") or "unknown").lower()
        if is_overdue and status == "open":
            status = "overdue"
        elif status not in {"paid", "open", "overdue"}:
            if is_overdue or (due and due < date.today() and status != "paid"):
                status = "overdue"
            elif status in {"unpaid", "pending", "billed"}:
                status = "open"
            else:
                status = "unknown"
        return Invoice(
            invoice_id=str(raw.get("invoiceId") or raw.get("id") or raw.get("number")),
            amount=_to_float(raw.get("amount") or raw.get("totalAmount")) or 0.0,
            currency=str(raw.get("currency") or "EUR"),
            due_date=due,
            status=status,
            issued_date=_to_date(raw.get("issuedDate") or raw.get("invoiceDate")),
            period_start=_to_date(raw.get("periodStart")),
            period_end=_to_date(raw.get("periodEnd")),
            amount_open=_to_float(raw.get("amountOpen")),
            is_overdue=is_overdue,
        )

    def _extract_address(self, item: dict[str, Any]) -> str | None:
        for key in ("deliveryAddress", "address", "streetAddress"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                street = " ".join(filter(None, [
                    value.get("streetName") or value.get("street"),
                    value.get("houseNumber"),
                ]))
                city = value.get("postOffice") or value.get("city")
                parts = [street, value.get("postalCode"), city]
                joined = " ".join(p for p in parts if p)
                if joined.strip():
                    return joined.strip()
        return None

    def _extract_fuse(self, item: dict[str, Any]) -> int | None:
        for key in ("fuseSize", "mainFuseSize", "mainFuse", "fuse"):
            value = item.get(key)
            if value is None:
                continue
            s = str(value).strip().lower()
            # "3x50" (3-phase × 50 A) → 50; "50" → 50; "50a" → 50
            amps_part = s.split("x", 1)[1] if "x" in s else s
            digits = "".join(c for c in amps_part if c.isdigit())
            if digits:
                return int(digits)
        return None

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        allow_missing: bool = False,
        _retry: bool = True,
    ) -> Any:
        token = await self._auth.async_ensure_token()
        headers = {"Authorization": f"Bearer {token}"}
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        try:
            async with self._session.get(url, params=params, headers=headers, timeout=timeout) as resp:
                if resp.status == 401 and _retry:
                    await self._auth.async_invalidate_token()
                    return await self._get_json(url, params=params, allow_missing=allow_missing, _retry=False)
                if resp.status == 401:
                    raise CarunaAuthError(f"401 after retry on {url}")
                if resp.status == 404 and allow_missing:
                    return None
                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else None
                    except ValueError:
                        wait = None
                    raise CarunaRateLimitError(retry_after=wait)
                if resp.status >= 500:
                    raise CarunaConnectionError(f"GET {url} → {resp.status}")
                if resp.status >= 400:
                    if allow_missing:
                        return None
                    raise CarunaAPIError(f"GET {url} → {resp.status}")
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise CarunaConnectionError(f"GET {url} network error: {err}") from err


__all__ = [
    "CarunaAPIError",
    "CarunaAuthError",
    "CarunaConnectionError",
    "CarunaPlusClient",
    "CarunaRateLimitError",
]
