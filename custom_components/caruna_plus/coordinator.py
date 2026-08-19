"""Coordinator with three sub-fetchers so failures stay isolated."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.models.statistics import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    CarunaAPIError,
    CarunaAuthError,
    CarunaConnectionError,
    CarunaPlusClient,
    CarunaRateLimitError,
)
from .const import (
    BILLING_FETCH_INTERVAL,
    CONF_ENABLE_HOURLY,
    CONF_UPDATE_INTERVAL,
    CONTRACT_FETCH_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    LTS_BACKFILL_DAYS,
    LTS_SOURCE,
    LTS_STATISTIC_ID_TEMPLATE,
)
from .models import Asset, BillingSnapshot, EnergySeries, PricePlan

_LOGGER = logging.getLogger(__name__)


@dataclass
class CarunaPlusData:
    """Merged view returned by the coordinator each cycle."""

    assets: list[Asset] = field(default_factory=list)
    energy_daily: dict[str, EnergySeries] = field(default_factory=dict)
    energy_hourly: dict[str, EnergySeries] = field(default_factory=dict)
    prices: dict[str, PricePlan] = field(default_factory=dict)
    billing: BillingSnapshot | None = None
    last_success: dict[str, datetime] = field(default_factory=dict)

    def slice_is_stale(self, key: str, max_age: timedelta) -> bool:
        ts = self.last_success.get(key)
        if ts is None:
            return True
        return (datetime.now(UTC) - ts) > max_age


class CarunaPlusCoordinator(DataUpdateCoordinator[CarunaPlusData]):
    """Owns all Caruna+ data for one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CarunaPlusClient,
        customer: str | None,
    ) -> None:
        interval_min = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({customer or 'unknown'})",
            update_interval=timedelta(minutes=interval_min),
            config_entry=entry,
        )
        self.entry = entry
        self.client = client
        self.customer: str | None = customer
        self._enable_hourly: bool = entry.options.get(CONF_ENABLE_HOURLY, True)

        self._contract_next_fetch: datetime = datetime.min.replace(tzinfo=UTC)
        self._billing_next_fetch: datetime = datetime.min.replace(tzinfo=UTC)
        self._data = CarunaPlusData()
        self._lts_backfilled: set[str] = set()

    async def _async_update_data(self) -> CarunaPlusData:
        if self.customer is None:
            # Fall back to whatever the token store knows about.
            customers = await self._safe_call(self.client.async_get_customers)
            if not customers:
                raise UpdateFailed("No customers available")
            self.customer = customers[0].number

        now = datetime.now(UTC)
        errors: list[str] = []

        # --- contract slice (daily) ---
        if now >= self._contract_next_fetch or not self._data.assets:
            try:
                assets = await self.client.async_get_assets(self.customer)
                self._data.assets = assets
                self._data.last_success["contract"] = now
                self._contract_next_fetch = now + CONTRACT_FETCH_INTERVAL
                # Prices change on the same cadence as contracts.
                try:
                    await self._fetch_prices()
                    self._data.last_success["prices"] = now
                except (CarunaConnectionError, CarunaAPIError) as price_err:
                    _LOGGER.debug("Price fetch failed (non-fatal): %s", price_err)
            except CarunaAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except (CarunaConnectionError, CarunaAPIError) as err:
                errors.append(f"contract: {err}")
                _LOGGER.warning("Contract fetch failed: %s", err)

        # --- energy slice (every cycle) ---
        try:
            await self._fetch_energy()
            self._data.last_success["energy"] = now
        except CarunaRateLimitError as err:
            wait = err.retry_after or 60
            _LOGGER.warning("Rate limited on energy; backing off %ss", wait)
            errors.append(f"energy: rate-limited (retry after {wait}s)")
        except CarunaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (CarunaConnectionError, CarunaAPIError) as err:
            errors.append(f"energy: {err}")
            _LOGGER.warning("Energy fetch failed: %s", err)

        # --- billing slice (every 6h) ---
        if now >= self._billing_next_fetch:
            try:
                self._data.billing = await self.client.async_get_billing(self.customer)
                self._data.last_success["billing"] = now
                self._billing_next_fetch = now + BILLING_FETCH_INTERVAL
            except CarunaAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except (CarunaConnectionError, CarunaAPIError) as err:
                errors.append(f"billing: {err}")
                _LOGGER.warning("Billing fetch failed: %s", err)

        # LTS backfill / append — non-fatal
        try:
            await self._sync_long_term_statistics()
        except Exception:
            _LOGGER.exception("LTS sync failed")

        # Only fail the entire update if the contract (assets) slice also
        # failed and we have no cached data at all — energy being unavailable
        # is not a hard failure, the contract sensors can still populate.
        if errors and not self._data.assets:
            raise UpdateFailed("; ".join(errors))
        return self._data

    async def _safe_call(self, coro_factory: Any) -> Any:
        try:
            return await coro_factory()
        except CarunaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CarunaConnectionError as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch_energy(self) -> None:
        today = date.today()
        for asset in self._data.assets:
            mp = asset.metering_point_id
            # API "daily" timespan returns 24 hourly-granularity points for a given day.
            # Fetch today so the energy_today sensor shows current-day usage.
            hourly = await self.client.async_get_energy(self.customer, mp, today, "daily")
            self._data.energy_hourly[mp] = hourly
            # API "monthly" timespan returns one daily-aggregate point per day in the month.
            # Used for energy_yesterday and energy_month_to_date sensors.
            monthly = await self.client.async_get_energy(self.customer, mp, today, "monthly")
            self._data.energy_daily[mp] = monthly

    async def _fetch_prices(self) -> None:
        for asset in self._data.assets:
            try:
                plan = await self.client.async_get_prices(self.customer, asset.metering_point_id)
            except (CarunaConnectionError, CarunaAPIError) as err:
                _LOGGER.debug("Price for %s failed: %s", asset.metering_point_id, err)
                continue
            self._data.prices[asset.metering_point_id] = plan

    async def _sync_long_term_statistics(self) -> None:
        """Backfill on first run, append on subsequent runs."""
        for mp_id, series in self._data.energy_hourly.items():
            statistic_id = LTS_STATISTIC_ID_TEMPLATE.format(mp=mp_id)
            metadata = StatisticMetaData(
                has_mean=False,
                has_sum=True,
                mean_type=StatisticMeanType.NONE,
                name=f"Caruna+ {mp_id} energy",
                source=LTS_SOURCE,
                statistic_id=statistic_id,
                unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            )

            if mp_id not in self._lts_backfilled:
                await self._backfill_statistics(mp_id, statistic_id, metadata)
                self._lts_backfilled.add(mp_id)
                continue

            last = await self.hass.async_add_executor_job(
                get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
            )
            last_sum = 0.0
            last_ts: datetime | None = None
            if last and last.get(statistic_id):
                entry = last[statistic_id][0]
                last_sum = float(entry.get("sum") or 0.0)
                start = entry.get("start")
                if isinstance(start, (int, float)):
                    last_ts = datetime.fromtimestamp(start, tz=UTC)
                elif isinstance(start, str):
                    last_ts = datetime.fromisoformat(start)

            stats: list[StatisticData] = []
            running = last_sum
            for point in series.points:
                if last_ts and point.timestamp <= last_ts:
                    continue
                running += point.kwh
                stats.append(
                    StatisticData(start=point.timestamp, state=point.kwh, sum=running)
                )
            if stats:
                async_add_external_statistics(self.hass, metadata, stats)

    async def _backfill_statistics(
        self, mp_id: str, statistic_id: str, metadata: StatisticMetaData
    ) -> None:
        today = date.today()
        stats: list[StatisticData] = []
        running = 0.0
        for offset in range(LTS_BACKFILL_DAYS, 0, -1):
            target = today - timedelta(days=offset)
            try:
                series = await self.client.async_get_energy(
                    self.customer, mp_id, target, "daily"
                )
            except (CarunaConnectionError, CarunaAPIError):
                continue
            for point in series.points:
                running += point.kwh
                stats.append(
                    StatisticData(start=point.timestamp, state=point.kwh, sum=running)
                )
        if stats:
            async_add_external_statistics(self.hass, metadata, stats)
