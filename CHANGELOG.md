# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.2] - 2026-08-18

### Added
- GitHub Actions release workflow — push a `v*` tag to automatically validate and publish a release with a zip asset.

## [0.2.1] - 2026-08-18

### Added
- Deploy script now prints the version transition (`0.x.y → 0.x.z`) before deploying and the confirmed deployed version after.

## [0.2.0] - 2026-08-18

### Added
- Swedish translation (`sv.json`) covering all config flow strings, sensor names, and binary sensor names.
- Integration icon (`icon.png`) at repo root and inside the component directory for HACS store listing.
- `suggested_display_precision` on all numeric sensors (0 for amps, 2 for EUR totals, 5 for EUR/kWh unit prices).
- `configuration_url="https://plus.caruna.fi"` on both DeviceInfo blocks (asset device and customer device).
- Cost fields on `EnergyPoint` model: `total_fee`, `distribution_fee`, `distribution_base_fee`, `electricity_tax`, `vat`, `temperature`.
- Cost sensors now derive values from energy response fields when no separate prices endpoint is available.
- `StatisticMeanType.NONE` on LTS `StatisticMetaData` to silence HA 2026.11 deprecation warning.

### Fixed
- Energy endpoint: `timespan=monthly` no longer sends `day=` parameter (was causing HTTP 400).
- Energy field name: use `totalConsumption` (confirmed from API) as primary field.
- Invoice endpoint: now fetches `?status=open` and `?status=paid` separately and merges results (bare path returns HTTP 400).
- `state_class` on MONETARY sensors: `basic_fee_monthly` and `cost_projected_month` have no `state_class`; `cost_month_to_date` and `year_to_date_spend` use `TOTAL` (HA only allows `None` or `TOTAL` with `device_class=MONETARY`).
- `DeviceEntryType.SERVICE` now imported from `homeassistant.helpers.device_registry` instead of using raw string `"service"`.

## [0.1.0] - 2026-08-13

### Added
- Initial release: config flow, DataUpdateCoordinator with isolated sub-fetchers, async Caruna+ API client with full 7-step Wicket/OAuth2 login chain, consumption sensors, contract sensors, cost sensors, billing sensors, binary sensors for invoice status, long-term statistics (LTS) backfill, diagnostics, English and Finnish translations, CI (hassfest + HACS validation + pytest).
