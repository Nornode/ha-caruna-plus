# Caruna+ for Home Assistant

Custom integration for [plus.caruna.fi](https://plus.caruna.fi/) — pulls consumption, contract, cost and billing data into Home Assistant and feeds the Energy dashboard via Long-Term Statistics.

> **⚠️ Not real-time.** Caruna publishes meter readings in daily batches with a **12–36 hour delay**. There is no live wattage feed from the source. Polling faster than every 15 minutes wastes API calls without ever returning newer data.
>
> **⚠️ Alpha.** The auth flow is implemented from [pycaruna](https://github.com/kimmolinna/pycaruna)'s documented shape and needs to be verified against a real HAR capture before v0.1.0 is tagged. Contract + invoice endpoint paths are placeholders (marked `TODO(har)` in the code).

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

- Complete the Wicket redirect chain used by Caruna's IDP.
- Prompt for an MFA code if the account requires one (SMS/TOTP support is best-effort until we've seen a real challenge).
- Auto-select the customer if only one is on the account; otherwise ask you to pick one.
- Persist the bearer token so restarts don't re-authenticate needlessly.

### Options (Configure → Options)

| Option | Default | Notes |
|---|---|---|
| Update interval | 60 min | Minimum 15 min. Anything shorter is wasted; Caruna's data is batch-published. |
| Fetch hourly consumption | on | Needed for the Energy dashboard. Turn off only if you don't care about hourly granularity. |

---

## Sensors

Grouped per metering point. Diagnostic entities (fuse, address, meter serial…) are hidden from dashboards by default; use the Devices page to inspect them.

### Consumption

- `sensor.energy_today` — `kWh`, resets at midnight.
- `sensor.energy_yesterday` — `kWh`.
- `sensor.energy_this_month` — `kWh`, month-to-date.
- `sensor.last_reading_time` — timestamp of the newest reading Caruna has published. Watch this to see how stale the data is.
- **Long-Term Statistics** feed `caruna_plus:<mp>_energy` — this is what the Energy dashboard consumes. Backfilled 30 days on first setup.

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
- `binary_sensor.invoice_overdue` — problem class.
- `binary_sensor.invoice_due_soon` — true within 7 days of due date.

---

## Energy dashboard setup

1. **Settings → Dashboards → Energy**.
2. Under **Electricity grid**, add a consumption source and pick the statistic named `caruna_plus:<metering-point-id>_energy`.
3. Optionally add the `sensor.energy_price` sensor as the price source.

Because Caruna is batch-published, the Energy dashboard will show yesterday's day fully — today's hours fill in the next morning.

---

## Troubleshooting

- **Login fails immediately** — Caruna's Wicket IDP is fragile. Enable debug logging (`custom_components.caruna_plus: debug` under `logger:` in `configuration.yaml`), reproduce, and file an issue with the log excerpt.
- **Sensors are `unavailable`** — the coordinator returned a stale slice. Check the last_success timestamps in **Diagnostics** (Devices & Services → Caruna+ → 3-dot menu → Download diagnostics).
- **MFA never completes** — MFA detection is heuristic until we've captured a real one. Open an issue with the redacted AJAX response body.
- **Cost sensors show `unknown`** — the price endpoint hasn't been verified from a HAR yet; the fallback derives unit prices from the last invoice. If you have no invoices yet, cost sensors stay `unknown`.

---

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt ruff mypy
pytest
```

Local Home Assistant testing:

```bash
mkdir -p config/custom_components
ln -s "$PWD/custom_components/caruna_plus" config/custom_components/caruna_plus
hass -c ./config
```

Contributions welcome. Before opening a PR on the auth flow, please capture a HAR of a fresh login and attach a redacted extract — that's the only way we validate the Wicket chain.

---

## Credits

- [`kimmolinna/pycaruna`](https://github.com/kimmolinna/pycaruna) — the original Python client whose endpoint reverse-engineering is the base for this integration.

## License

MIT — see [LICENSE](LICENSE).
