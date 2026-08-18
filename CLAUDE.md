# CLAUDE.md — Caruna Plus HA Integration

Project knowledge for the AI assistant. Automatically loaded each session.

---

## What this project is

A HACS-distributable Home Assistant custom integration for **Caruna PLUS**
(`plus.caruna.fi`), the Finnish electricity distribution portal. It exposes
energy consumption, contract info, and billing as HA sensors with long-term
statistics (LTS) support.

---

## Python environment

Always activate `.venv` before running any Python or script command:

```bash
source .venv/bin/activate
```

The system Python is externally managed (Homebrew). **Never** `pip install`
without the venv active.

---

## Running the smoke test

```bash
bash scripts/smoke.local.sh        # credentials are hardcoded in that file
```

`scripts/smoke.local.sh` is gitignored (`scripts/*.local.sh`). It sets
`CARUNA_USERNAME` and `CARUNA_PASSWORD` then calls `scripts/smoke_login.py`.
The script runs the full 7-step auth chain and then fetches `/assets` to
verify field names.

---

## Deploying to the test HA instance

```bash
bash scripts/deploy.local.sh            # rsync only
bash scripts/deploy.local.sh --restart  # rsync + restart core
```

- Host: `192.168.1.100`, user: `homeassistant`, port: 22
- Destination: `/config/custom_components/caruna_plus/`
- The `/config/custom_components/` directory must exist first (the script
  creates it with `ssh mkdir -p` before rsyncing).

---

## Authentication chain — critical knowledge

The login flow is a **7-step Wicket/WSO2/OAuth2 chain**. All of it is
implemented in `custom_components/caruna_plus/auth.py`.

| Step | What happens |
|------|-------------|
| 1 | POST `plus.caruna.fi/api/authorization/login` with `{redirectAfterLogin, language}` → JSON with `loginRedirectUrl` |
| 2 | GET `loginRedirectUrl` → HTML page with `<meta http-equiv="refresh">` pointing to IDP |
| 3 | GET IDP (`authentication2.caruna.fi/portal/ngpostResponder?...`) → Wicket login HTML form |
| 4 | POST credentials to Wicket AJAX URL with special headers → CDATA response with next URL |
| 5 | GET next URL (`wicket/page?4`) → meta-refresh to `ngpostResponder` |
| 6 | GET `ngpostResponder` → auto-submit form; POST its `resp` field to `commonauth` → 302 to `oauth2/authorize?sessionDataKey=…` |
| 7 | GET `oauth2/authorize?sessionDataKey=…` with **`allow_redirects=False`** → 302 to `plus.caruna.fi/openid-login-return?code=…&state=…&session_state=…`; extract params; POST **form-encoded** to `EP_TOKEN` |

### Non-obvious bugs already fixed — do not regress

- **`resp.json(content_type=None)`** everywhere: Caruna's server returns JSON
  with `Content-Type: text/html`. Without `content_type=None` aiohttp raises
  `ContentTypeError`. This applies in **both** `auth.py` (step 1, step 7) and
  `api.py` (`_get_json`).

- **Wicket AJAX URL format**: The form action is `?{page}-{version}.-{path}`
  (no behaviour index). The AJAX URL needs `?{page}-{version}.0-{path}-{button}`
  (insert `0` after the dot). Use:
  ```python
  ajax_base = re.sub(r"(\d+\.)(-)", r"\g<1>0\2", form_base_url)
  ```

- **EP_TOKEN must be called with `data=` (form-encoded), not `json=`.**
  Posting JSON returns `{"msg":"Authentication state not found."}` (HTTP 400).

- **Do NOT follow the redirect to `openid-login-return`** before posting to
  EP_TOKEN. Following that page appears to consume server-side OAuth2 state.
  Stop at the redirect, extract `code`/`state`/`session_state` from the
  `Location` header, then POST directly.

- **`BeautifulSoup` parser**: use `"html.parser"`, not `"xml"`. The `xml`
  parser requires `lxml` which is not in the venv.

---

## API notes

All endpoints confirmed from `scripts/probe_endpoints.local.sh` run on 2026-08-18.

- `EP_ASSETS` (`/api/customers/{customer}/assets`) — **confirmed**.
  Asset keys: `assetId`, `gsrn`, `contractId`, `contractType`, `contractProductDesc`,
  `fuseSize`, `address`, `currentCounterSerialNumber`, `customerId`.

- `EP_ENERGY` (`/api/customers/{customer}/assets/{mp}/energy`) — **confirmed**.
  Use `?year=&month=&day=&timespan=daily` (returns 24 hourly rows per day).
  **`timespan=hourly` does not exist — returns 400 "Request data missing".**
  Use `timespan=monthly` for daily aggregates (one row per day of month).
  Row keys: `timestamp`, `totalConsumption`, `invoicedConsumption`, `totalFee`,
  `distributionFee`, `distributionBaseFee`, `electricityTax`, `valueAddedTax`, `temperature`.

- `EP_INVOICES` (`/api/customers/{customer}/invoices`) — **confirmed**.
  Requires `?status=open` or `?status=paid`. Returns `compressedInvoices` columnar
  format (decoded by `_decompress_invoices` in `api.py`).
  Decoded keys: `id`, `amount`, `amountOpen`, `vatExcluded`, `vatAmount`, `dueDate`,
  `invoiceDate`, `isOverdue`, `status` (injected = query param value), `assets`,
  `reference`, `virtualBarCode`, `maxDueDate`, `billingType`.
  No `periodStart`/`periodEnd` — those fields are absent from the list endpoint.

- `EP_PRICES` — **does not exist**. All 5 candidate paths return 404.
  Price information must be derived from invoice detail line items
  (`_derive_price_from_invoices` in `api.py`).

- `EP_INVOICE` (detail endpoint) — **unconfirmed**. The list endpoint has no
  line-item detail. The path `/api/customers/{customer}/invoices/{id}` is a
  placeholder that may not exist.

---

## Sensitive / gitignored files

| Path | What it is |
|------|-----------|
| `scripts/*.local.sh` | Scripts with hardcoded credentials (e.g. `smoke.local.sh`) |
| `docs/har/` | HAR captures containing tokens and personal data |
| `tests/fixtures/*.local.*` | Local test fixtures with real data |

**Never commit any of these.** The `.gitignore` already excludes them.

---

## Test HA instance

URL: `http://192.168.1.100:8123/`  
Used for Phase C testing (live install). Deploy with the deploy script above.

---

## Reference implementations (studied for auth flow)

- `github.com/kimmolinna/pycaruna` — minimal synchronous Python, shows the
  correct final POST format: `data=r.request.path_url.split("?")[1]`
- `github.com/Jalle19/pycaruna` — more detailed; explicitly does manual
  redirect-following to extract `code`/`state` without loading the callback
  page, then `data=connect2id_params` to token endpoint.

---

## Current status (as of 2026-08-18)

- **Phase A (smoke test)**: PASSING — full auth chain works, token obtained.
- **Phase B (endpoint probe)**: DONE — all endpoints confirmed; see API notes above.
- **Phase C (live HA install)**: In progress — redeploy needed to verify sensors populate.

### Known remaining work

1. Redeploy and confirm contract sensors (fuse, address, tariff) show data.
2. Verify energy sensors backfill into LTS correctly.
3. `EP_INVOICE` (detail) path unconfirmed — may not exist; if 404 silently,
   `_derive_price_from_invoices` will be used (acceptable fallback).
4. Write tests (`test_api.py`, `test_config_flow.py`, `test_coordinator.py`).
