# Caruna+ for Home Assistant

Custom integration for [plus.caruna.fi](https://plus.caruna.fi/) — pulls consumption, contract, cost and billing data into Home Assistant and feeds the Energy dashboard via Long-Term Statistics.

> **⚠️ Not real-time.** Caruna publishes meter readings in daily batches with a **12–36 hour delay**. There is no live wattage feed from the source. Polling faster than every 15 minutes wastes API calls without ever returning newer data.
>
> **Beta.** The full 7-step Wicket/OAuth2 login chain and all data endpoints (assets, energy, invoices) are confirmed working against the live service. There is no dedicated prices endpoint — cost sensors derive unit prices from invoice line items as a fallback. MFA challenge detection is still heuristic since no real MFA-enabled account has been tested against it yet.

---

## Install

### HACS (custom repository, until listed in default)

1. HACS → Integrations → menu → **Custom repositories**.
2. Add `https://github.com/Nornode/ha-caruna-plus` with category **Integration**.
3. Install "Caruna+" and restart Home Assistant.
4. **Settings → Devices & Services → Add integration → Caruna+**.

### Manual

Copy `custom_components/caruna_plus/` into your Home Assistant `config/custom_components/` directory and restart.

---

## Configure

The setup form asks for your `plus.caruna.fi` username and password. On login, the integration will:

- Complete the Wicket/WSO2/OAuth2 redirect chain used by Caruna's IDP.
- Prompt for an MFA code if the account requires one (SMS/TOTP support is best-effort until we've seen a real challenge).
- Auto-select the customer if only one is on the account; otherwise ask you to pick one.
- Persist the bearer token so restarts don't re-authenticate needlessly, and transparently re-login when it expires.

If your password changes or the token can no longer be refreshed, Home Assistant will prompt a reauth flow asking only for the new password.

### Options (Configure → Options)

| Option | Default | Notes |
|---|---|---|
| Update interval | 60 min | Minimum 15 min, maximum 24 h. Anything shorter than 15 min is wasted; Caruna's data is batch-published. |
| Fetch hourly consumption | on | Needed for the Energy dashboard. Turn off only if you don't care about hourly granularity. |

---

## Sensors

Grouped per metering point. Diagnostic entities (fuse, address, meter serial, token expiry…) are hidden from dashboards by default; use the Devices page to inspect them.

### Consumption

- **Energy today** (`kWh`) — resets at midnight.
- **Energy yesterday** (`kWh`).
- **Energy this month** (`kWh`) — month-to-date.
- **Energy this month last year** (`kWh`) — same month-to-date range, one year back, for comparison.
- **Last reading time** — timestamp of the newest reading Caruna has published. Watch this to see how stale the data is.
- **Long-Term Statistics** feed `caruna_plus:<metering-point-id>_energy` — this is what the Energy dashboard consumes. Backfilled 30 days on first setup.

### Contract (diagnostic)

- Main fuse (A)
- Contract type (`Yleissähkö`, `Aikasähkö`, …)
- Tariff
- Delivery address
- Meter serial

### Cost

- Energy price (€/kWh)
- Transfer fee (€/kWh)
- Electricity tax (€/kWh)
- Basic fee (€/month)
- Cost this month (€) — computed from consumption × current tariff + prorated basic fee.
- Projected cost this month (€) — linear extrapolation from month-to-date.

### Billing (per customer)

- Last invoice amount (€)
- Last invoice due date
- Last invoice status (`paid` / `open` / `overdue`)
- Next invoice estimate (€)
- Year to date spend (€)
- **Invoice overdue** (`binary_sensor`, problem class).
- **Invoice due soon** (`binary_sensor`) — true within 7 days of due date.

### Diagnostic (integration health, disabled by default)

- Token expires — when the current bearer token needs renewal.
- Last successful update — timestamp of the last coordinator refresh that succeeded.
- Last error — the most recent per-slice error category (auth / connection / rate limited / API), if any.

---

## Energy dashboard setup

1. **Settings → Dashboards → Energy**.
2. Under **Electricity grid**, add a consumption source and pick the statistic named `caruna_plus:<metering-point-id>_energy`.
3. Optionally add the **Energy price** sensor as the price source.

Because Caruna is batch-published, the Energy dashboard will show yesterday's day fully — today's hours fill in the next morning.

---

## Troubleshooting

- **Login fails immediately** — Caruna's Wicket IDP is fragile. Enable debug logging (`custom_components.caruna_plus: debug` under `logger:` in `configuration.yaml`), reproduce, and file an issue with the log excerpt.
- **Sensors are `unavailable`** — the coordinator returned a stale slice. Check the diagnostic sensors (Token expires / Last successful update / Last error) or download full diagnostics (Devices & Services → Caruna+ → 3-dot menu → Download diagnostics).
- **MFA never completes** — MFA detection is heuristic until we've captured a real challenge. Open an issue with the redacted AJAX response body.
- **Cost sensors show `unknown`** — there is no dedicated prices endpoint; unit prices are derived from the latest invoice's line items. If you have no invoices yet, cost sensors stay `unknown` until the first one arrives.

---

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-test.txt ruff mypy
pytest
```

Smoke-test the live login/API chain against a real account (credentials in a gitignored `scripts/smoke.local.sh`, see [docs/TESTING.md](docs/TESTING.md)):

```bash
bash scripts/smoke.local.sh
```

Deploy to a test Home Assistant instance over SSH:

```bash
bash scripts/deploy.local.sh            # rsync only
bash scripts/deploy.local.sh --restart  # rsync + restart core
```

Contributions welcome. Before changing the auth flow, please smoke-test it against a real account — that's the only way we validate the Wicket chain.

---

## Credits

- [`kimmolinna/pycaruna`](https://github.com/kimmolinna/pycaruna) and [`Jalle19/pycaruna`](https://github.com/Jalle19/pycaruna) — prior Python clients whose reverse-engineered login flow and endpoints informed this integration's async implementation.

## License

MIT — see [LICENSE](LICENSE).
