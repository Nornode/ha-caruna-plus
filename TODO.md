# Caruna+ Home Assistant Integration — TODO

A living checklist for building a HACS-distributable, HA-native custom integration for [plus.caruna.fi](https://plus.caruna.fi/).

Existing work to lean on: [`kimmolinna/pycaruna`](https://github.com/kimmolinna/pycaruna) — covers login + hourly consumption but is sync, unpackaged, and doesn't handle token refresh, MFA, contracts, or billing. We will rewrite it as an async client inside this repo, not import it.

---

## 0. Scope & non-goals

- **In scope (v0.1):**
  - Robust login with username/password (auto-retry on stale token, MFA-aware, session persistence across HA restarts).
  - List metering points + contract metadata.
  - **Consumption sensors** (daily + hourly) fed into Long-Term Statistics for the Energy dashboard.
  - **Contract sensors** — main fuse size, contract type, tariff name, delivery address, meter serial.
  - **Cost sensors** — energy price (c/kWh), transfer fee, electricity tax, current-period spend, projected month spend.
  - **Billing sensors** — last invoice amount + status + due date, next invoice estimate, YTD spend.
  - Config flow with reauth + options flow (polling interval, currency, enable hourly).
  - Multi-customer accounts.
- **In scope (v0.2+):**
  - Solar / bidirectional (production) support.
  - CO₂ intensity + tariff-time-of-use breakdown.
  - Push to HA core.
- **Out of scope:**
  - Write operations back to Caruna (none exposed).
  - Real-time meter readings — **Caruna does not publish these**; see §1a below.

---

## 1. Understand the upstream service

- [ ] Log in manually to `https://plus.caruna.fi/` and capture a full network trace (browser devtools → HAR export). Save into `docs/har/` (gitignored — contains cookies + tokens).
- [ ] Repeat the capture for the pages that show: dashboard, hourly consumption, contract, invoices, invoice detail, usage report. Each is a separate REST call worth documenting.
- [ ] Confirm endpoint list from `pycaruna` still matches today's site:
  - `POST /api/authorization/login` → returns `loginRedirectUrl`.
  - Redirect chain through `https://authentication2.caruna.fi/portal/` (Apache Wicket IDP).
  - `POST /api/authorization/token` → bearer token.
  - `GET /api/customers/{customer}/assets` → metering points + likely contract fields.
  - `GET /api/customers/{customer}/assets/{mp}/energy?year=&month=&day=&timespan=daily|hourly` → consumption.
- [ ] Discover + document the endpoints pycaruna does NOT cover (guess names — verify from HAR):
  - Contract detail: `/api/customers/{customer}/assets/{mp}/contract` or embedded in assets response. Extract fuse (`mainFuseSize`), contract type, tariff, delivery address, meter serial.
  - Invoice list: `/api/customers/{customer}/invoices` (or `/billing/invoices`).
  - Invoice detail (line items): `/api/customers/{customer}/invoices/{id}`.
  - Price / tariff: often on the contract object; if not, look for `/api/customers/{customer}/prices` or `/tariffs`.
- [ ] Inspect the JWT (`jwt.io` locally, offline) — record `exp`, `iat`, `iss`, refresh strategy, whether a `refresh_token` cookie exists.
- [ ] Determine whether Caruna enforces MFA (SMS / TOTP / bank ID). If yes, note the exact form fields + which step in the Wicket chain injects it.
- [ ] Note rate-limit behaviour (429s, retry-after headers) and typical response latency.
- [ ] Write everything up in `docs/api-notes.md` as we go — the endpoint reference we'll rely on for the next 12 months.

### 1a. Data-freshness reality check (important — read before promising "live" data)

Caruna is a distribution network operator, not a smart-plug vendor. Their portal shows meter readings **published in daily batches with a delay of 12–36 hours**. There is no push channel, no websocket, no near-real-time meter feed. Concretely:

- Yesterday's hourly readings typically appear by mid-morning today.
- The current day's hourly readings appear the next morning.
- The "live" tile some users expect (a wattage right now) **does not exist** in the source data.

Implications for this integration:
- Default polling interval: **60 minutes**. Faster polling wastes API calls and hits rate limits without ever returning newer data.
- We expose a `sensor.caruna_plus_<mp>_last_reading_time` so users can see when the newest hourly reading actually landed.
- Options-flow `update_interval` lets power users go as low as 15 min if they want to catch the morning refresh sooner, but the README will say plainly: **not real-time**.
- Long-Term Statistics are backfilled by timestamp — this is what makes the Energy dashboard correct even though data arrives late.

---

## 2. Repository layout (HACS-compliant)

HACS requires everything under `custom_components/<domain>/`, one integration per repo, plus `hacs.json` at the root and brand assets.

```
ha-caruna-plus-integration/
├── custom_components/
│   └── caruna_plus/
│       ├── __init__.py
│       ├── manifest.json
│       ├── const.py
│       ├── config_flow.py
│       ├── coordinator.py
│       ├── api.py                # async Caruna+ client
│       ├── auth.py               # login + token store (split for clarity)
│       ├── sensor.py
│       ├── binary_sensor.py      # e.g. invoice_overdue
│       ├── diagnostics.py
│       ├── strings.json
│       ├── translations/
│       │   ├── en.json
│       │   └── fi.json
│       └── quality_scale.yaml
├── hacs.json
├── README.md
├── LICENSE                       # MIT to match pycaruna
├── info.md
├── .github/
│   ├── workflows/
│   │   ├── validate.yml          # hassfest + HACS action
│   │   └── tests.yml             # pytest on PR
│   └── ISSUE_TEMPLATE/
├── tests/
│   ├── conftest.py
│   ├── fixtures/                 # sanitized HTML + JSON responses
│   ├── test_auth.py
│   ├── test_api.py
│   ├── test_config_flow.py
│   └── test_coordinator.py
└── TODO.md
```

- [ ] Create `custom_components/caruna_plus/` skeleton (empty files first, wire up gradually).
- [ ] Add `hacs.json` at root:
  ```json
  {
    "name": "Caruna+",
    "render_readme": true,
    "homeassistant": "2024.10.0",
    "zip_release": false
  }
  ```
- [ ] Submit brand assets (`icon.png` + `dark_icon.png`, 256×256 and 512×512) to `home-assistant/brands` via PR — **prerequisite for HACS default listing**.

---

## 3. `manifest.json`

- [ ] Draft manifest:
  ```json
  {
    "domain": "caruna_plus",
    "name": "Caruna+",
    "version": "0.1.0",
    "documentation": "https://github.com/<owner>/ha-caruna-plus-integration",
    "issue_tracker": "https://github.com/<owner>/ha-caruna-plus-integration/issues",
    "codeowners": ["@<owner>"],
    "requirements": ["beautifulsoup4>=4.12"],
    "iot_class": "cloud_polling",
    "config_flow": true,
    "integration_type": "hub",
    "quality_scale": "silver"
  }
  ```
- Use `aiohttp` (already in HA core) — no need to declare it in requirements. Only `beautifulsoup4` for the Wicket HTML parsing.

---

## 4. Authentication — do it properly (`auth.py`)

This is the failure mode most home-brew Caruna integrations hit. Design it with these guarantees:

- [ ] **Never block the event loop.** All network I/O via `aiohttp`. HTML parsing via `BeautifulSoup` inside `await hass.async_add_executor_job(...)` (bs4 is sync CPU work).
- [ ] **Single source of truth for the token.** `TokenStore` dataclass with `access_token`, `expires_at`, `customer_number`, `refresh_cookie_jar`. Persisted to `entry.data["token"]` on change (encrypted-at-rest is HA's problem, not ours).
- [ ] **Preemptive refresh.** Any request checks `expires_at`. If <5 minutes to expiry, `async_login()` first, then proceed.
- [ ] **Reactive refresh.** If a request returns 401/403 despite a "fresh" token, invalidate and re-login once. Second failure → `CarunaAuthError` → `ConfigEntryAuthFailed` → HA reauth flow.
- [ ] **Concurrency-safe.** Guard `async_login()` with `asyncio.Lock` — if 4 sensors all trigger a refresh at once, only one login runs; others wait and reuse the result.
- [ ] **Full Wicket chain implemented as discrete steps** (each testable in isolation with a recorded HTML fixture):
  1. `POST /api/authorization/login` with `{"redirectAfterLogin": "https://plus.caruna.fi/", "language": "fi"}` → parse `loginRedirectUrl` JSON.
  2. `GET` that URL → parse `<meta http-equiv="refresh" content="0;URL=...">` to get IDP entry.
  3. `GET` IDP page → collect ALL `<input type="hidden">` fields, note the form `action`, append `0-userIDPanel-usernameLogin-loginWithUserID` component path.
  4. `POST` credentials as `ttqusername` + `userPassword` + hidden fields + button `"1"` with Wicket AJAX headers (`Wicket-Ajax: true`, `Wicket-Ajax-BaseURL: .`, `Wicket-FocusedElementId: loginWithUserID5`, `X-Requested-With: XMLHttpRequest`, `Origin`, `Referer`).
  5. Parse `CDATA[...]` from AJAX response → next URL.
  6. `GET` → another meta-refresh → final auto-submit form.
  7. `POST` final form → capture the query-string in the Location header, forward it to `POST /api/authorization/token` → bearer token JSON.
- [ ] **MFA detection.** After step 4, if the AJAX response contains a challenge form (SMS code, TOTP), raise `CarunaMFARequired(challenge_id, prompt)`. Config flow catches it and shows an `async_step_mfa`. Token-store caches `mfa_trust_cookie` if the IDP issues one so we don't re-prompt every 30 min.
- [ ] **Typed exceptions** (in `api.py`):
  - `CarunaAuthError` — bad password, account locked. → reauth.
  - `CarunaMFARequired` — needs user code. → config-flow step.
  - `CarunaRateLimitError` — 429. Coordinator honours `Retry-After`.
  - `CarunaConnectionError` — network / 5xx. → `UpdateFailed`.
  - `CarunaAPIError` — 4xx we didn't expect. → logged + `UpdateFailed`.
- [ ] **Unit tests** feed recorded HTML fixtures through each step. No live network in CI.

---

## 5. API client surface (`api.py`)

Wraps `auth.py` and exposes typed dataclasses. Each method transparently ensures an auth session before hitting the API.

- [ ] `CarunaPlusClient(session, username, password, token_store=None)`.
- [ ] `async def async_get_customers() -> list[Customer]` — extracted from login `info['user']`.
- [ ] `async def async_get_assets(customer) -> list[Asset]` — asset = metering point + embedded contract fields (fuse, tariff, address, meter serial).
- [ ] `async def async_get_energy(customer, mp, date_from, date_to, timespan) -> list[EnergyPoint]` — daily + hourly.
- [ ] `async def async_get_invoices(customer) -> list[Invoice]` — invoice list.
- [ ] `async def async_get_invoice(customer, invoice_id) -> InvoiceDetail` — line items, energy price, transfer, tax.
- [ ] `async def async_get_prices(customer, mp) -> PricePlan` — current tariff c/kWh + transfer fee. Falls back to invoice line-item averages if there's no dedicated endpoint.
- [ ] Dataclasses live in `models.py`, typed with `from __future__ import annotations` + `TypedDict` or `attrs` — pick whichever HA style guide prefers this month.

---

## 6. Config flow (`config_flow.py`)

- [ ] `async_step_user` — form with `CONF_USERNAME` + `CONF_PASSWORD`.
- [ ] Validate by attempting `async_login()`. On `CarunaMFARequired`, jump to `async_step_mfa`.
- [ ] `async_step_mfa` — form with `code`. Complete login. Success → unique ID = customer number.
- [ ] `async_step_reauth` — password only; preserve username.
- [ ] `async_step_reconfigure` — allow changing password later without deleting the entry.
- [ ] Multi-customer: if `ownCustomerNumbers` has >1 entry, present `async_step_select_customer`.
- [ ] Options flow — `update_interval` (15/30/60/120 min), `enable_hourly` (bool), `currency` (EUR default), `include_vat` (bool).
- [ ] Translations: `strings.json` + `translations/en.json` + `translations/fi.json`.

---

## 7. DataUpdateCoordinator (`coordinator.py`)

One coordinator per config entry, three concerns kept separate:

- [ ] `CarunaPlusCoordinator` — top-level, dispatches to sub-fetchers, returns a merged dict.
- [ ] Sub-fetchers, so each can fail independently without nuking the whole update:
  - `_fetch_contract()` — daily (contract rarely changes).
  - `_fetch_energy()` — every `update_interval`.
  - `_fetch_billing()` — every 6h (invoices don't change often).
- [ ] Track per-sub-fetcher `last_success_at` — sensors mark themselves unavailable if their slice is stale >24h even when other slices are fresh.
- [ ] Default `update_interval = 60 min`. Honour `Retry-After` on 429.
- [ ] On `CarunaAuthError` → `ConfigEntryAuthFailed`.
- [ ] On `CarunaConnectionError` / `CarunaAPIError` → `UpdateFailed`.
- [ ] Backfill Long-Term Statistics on first refresh (last 30 days of hourly), then append incrementally.

---

## 8. Entities

### 8a. Sensors (`sensor.py`)

Grouped by data slice. Every sensor uses `CoordinatorEntity` + `SensorEntity` and belongs to a `DeviceInfo` per metering point (so users see "Kotitalo (MP 643...)" as a device).

**Consumption**
- [ ] `sensor.caruna_plus_<mp>_energy_today` — `state_class=total_increasing`, `device_class=energy`, `unit=kWh`.
- [ ] `sensor.caruna_plus_<mp>_energy_yesterday` — `state_class=total`, resets daily.
- [ ] `sensor.caruna_plus_<mp>_energy_month_to_date`.
- [ ] `sensor.caruna_plus_<mp>_last_reading_time` — `device_class=timestamp`. Tells the user how stale the data is.
- [ ] Long-Term Statistics feed at `caruna_plus:<mp>_energy` via `async_add_external_statistics`. This is what the Energy dashboard consumes.

**Contract** (diagnostic entities — `entity_category=DIAGNOSTIC` so they don't clutter dashboards)
- [ ] `sensor.caruna_plus_<mp>_main_fuse` — value in amps, `unit=A`, `icon=mdi:fuse`.
- [ ] `sensor.caruna_plus_<mp>_contract_type` — e.g. "Yleissähkö" / "Aikasähkö".
- [ ] `sensor.caruna_plus_<mp>_tariff` — current tariff name.
- [ ] `sensor.caruna_plus_<mp>_delivery_address` — street address.
- [ ] `sensor.caruna_plus_<mp>_meter_serial` — physical meter ID.
- [ ] `sensor.caruna_plus_<mp>_metering_point_id` — GSRN.

**Cost / pricing**
- [ ] `sensor.caruna_plus_<mp>_energy_price` — c/kWh, `state_class=measurement`, `unit=EUR/kWh` (HA convention — some dashboards want `c/kWh`, pick one and document).
- [ ] `sensor.caruna_plus_<mp>_transfer_fee` — c/kWh.
- [ ] `sensor.caruna_plus_<mp>_electricity_tax` — c/kWh.
- [ ] `sensor.caruna_plus_<mp>_basic_fee_monthly` — €/month fixed.
- [ ] `sensor.caruna_plus_<mp>_cost_month_to_date` — €, computed = Σ(hourly kWh × total unit price) + basic_fee prorated. `state_class=total_increasing`.
- [ ] `sensor.caruna_plus_<mp>_cost_projected_month` — linear projection from MTD.

**Billing** (per customer, not per MP)
- [ ] `sensor.caruna_plus_<customer>_last_invoice_amount` — €.
- [ ] `sensor.caruna_plus_<customer>_last_invoice_due_date` — `device_class=timestamp`.
- [ ] `sensor.caruna_plus_<customer>_last_invoice_status` — paid / open / overdue.
- [ ] `sensor.caruna_plus_<customer>_next_invoice_estimate` — €.
- [ ] `sensor.caruna_plus_<customer>_year_to_date_spend` — €, `state_class=total_increasing`.

### 8b. Binary sensors (`binary_sensor.py`)

- [ ] `binary_sensor.caruna_plus_<customer>_invoice_overdue` — `device_class=problem`.
- [ ] `binary_sensor.caruna_plus_<customer>_invoice_due_soon` — true if any open invoice due within 7 days.

---

## 9. Wire it up (`__init__.py`)

- [ ] `async_setup_entry`:
  1. Rehydrate `TokenStore` from `entry.data`.
  2. Build client; call `async_login()` only if token missing/expired.
  3. Create coordinator; `await coordinator.async_config_entry_first_refresh()`.
  4. Store in `hass.data[DOMAIN][entry.entry_id]`.
  5. `await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR, Platform.BINARY_SENSOR])`.
- [ ] `async_unload_entry` — unload platforms, persist final `TokenStore`, clean up.
- [ ] `async_reload_entry` on options change.
- [ ] Register update-listener that reloads on options change but NOT on token-only changes.

---

## 10. Diagnostics + repairs

- [ ] `diagnostics.py` — dump redacted config entry + last coordinator data. Redact: username, tokens, cookies, metering-point IDs, address, meter serial, invoice IDs, monetary amounts.
- [ ] Register a repair issue if:
  - Login has been failing for >24h and user hasn't reauthed.
  - MFA cookie expired and re-prompt is needed.
  - Coordinator hasn't successfully updated in >48h.

---

## 11. Tests

- [ ] `tests/fixtures/` — sanitized HTML pages from the Wicket chain + JSON responses from every API call. **Scrub every real customer number / address / token before committing.**
- [ ] `test_auth.py` — each Wicket step in isolation; MFA challenge; token expiry preemptive refresh; concurrent-lock behaviour.
- [ ] `test_api.py` — each endpoint parser against a fixture.
- [ ] `test_config_flow.py` — happy, wrong-password, MFA, connection error, reauth, multi-customer.
- [ ] `test_coordinator.py` — success, partial failure (contract OK, energy fails), auth failure → `ConfigEntryAuthFailed`, rate limit honours `Retry-After`.
- [ ] Coverage target: 90%+.

---

## 12. CI

- [ ] `.github/workflows/validate.yml` — `home-assistant/actions/hassfest` + `hacs/action`.
- [ ] `.github/workflows/tests.yml` — pytest on Python 3.12 + 3.13.
- [ ] Pre-commit: `ruff`, `mypy --strict` on `custom_components/caruna_plus/`.

---

## 13. Docs

- [ ] `README.md` — HACS install, setup screenshots, complete sensor list, **"not real-time" callout**, Energy dashboard walkthrough, credit to pycaruna.
- [ ] `info.md` — short version HACS shows in-app.
- [ ] `docs/api-notes.md` — endpoint reference for maintainers.
- [ ] `CHANGELOG.md` — start from 0.1.0.

---

## 14. Release + HACS submission

- [ ] Tag `v0.1.0` on GitHub.
- [ ] Merge brand PR to `home-assistant/brands` (blocks HACS default listing).
- [ ] Open PR to `hacs/default`. Checks must pass: hassfest + HACS validation.
- [ ] Announce on HA community forum + r/homeassistant.

---

## 15. Post-v0.1 backlog

- [ ] Solar / bidirectional (production) support.
- [ ] Time-of-use tariff breakdown (yö/päivä sensors).
- [ ] CO₂ intensity if Caruna exposes network mix.
- [ ] Native services (`caruna_plus.refresh`, `caruna_plus.download_invoice`).
- [ ] Push to HA core.

---

## Skills / knowledge required

| Phase | Skill |
|---|---|
| API reverse-engineering | Reading HAR files, Wicket form quirks (hidden inputs, AJAX/CDATA responses), OAuth-ish redirect chains, JWT introspection |
| Auth robustness | `asyncio.Lock`, token persistence via `ConfigEntry.data`, preemptive vs reactive refresh, MFA state machines |
| Client library | `aiohttp`, `BeautifulSoup` in executor, retry patterns, typed exceptions, `pytest-asyncio`, fixture scrubbing |
| HA integration | `DataUpdateCoordinator` with sub-fetchers, config flow + reauth + reconfigure + MFA step + options flow, `SensorEntity` + `BinarySensorEntity` + `CoordinatorEntity`, `DeviceInfo`, `async_add_external_statistics`, `Retry-After` handling, translations, diagnostics, repair issues |
| Distribution | `manifest.json` fields, `hacs.json`, one-integration-per-repo rule, brands PR, hassfest + HACS Actions, semver, HACS default PR |
| Ongoing | `ruff`, `mypy --strict`, HA quality scale checklist, issue-template hygiene |

Reference the `ha-integration-dev` skill whenever picking up an HA-specific task.
