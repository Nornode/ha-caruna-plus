# Testing the Caruna+ integration

Practical procedure for validating the auth flow against a real account. Optimised for a fast feedback loop that produces actionable diagnostics when something breaks.

---

## 0. Terms in one line each

- **HAR**: JSON export from browser devtools' Network tab. Records every request/response, headers, cookies, bodies. Our "ground truth" for what a working login looks like.
- **Wicket**: Java web-UI framework Caruna uses for its login page. It's stateful (hidden component-path fields) and its AJAX responses are XML with `<![CDATA[…]]>` redirects. Our `auth.py` threads through 7 Wicket steps to complete a login.

---

## 1. Prerequisites

- A real `plus.caruna.fi` account you can log into from a browser.
- Access to your test Home Assistant at http://192.168.1.100:8123/.
- Shell access to that HA host (SSH add-on, Samba, or the machine directly) — you'll need to copy files into `config/custom_components/`.
- Python 3.12 on your workstation (for the standalone smoke test — faster than round-tripping through HA).

---

## 2. Feedback loop overview

Every failure mode below produces a specific artifact I can debug from. When something breaks, paste **the exact artifact named for that failure mode** back into the chat. Vague "it didn't work" reports mean guessing; the artifacts turn debugging into a few minutes of code changes.

| Failure mode | Artifact to send back |
|---|---|
| Standalone script fails at a specific Wicket step | The console output (script prints step-by-step) |
| Standalone script succeeds but HA install fails | HA log excerpt with `custom_components.caruna_plus` at DEBUG |
| Login succeeds but sensors are `unknown` | HA **Download Diagnostics** file from the integration |
| MFA prompt never appears (or appears when it shouldn't) | Redacted HAR of a manual browser login |
| Contract / invoice / price sensors are `unknown` | Same HAR — needed to fill in `TODO(har)` endpoints |

---

## 3. Phase A — standalone smoke test (do this first)

Runs the auth flow **outside Home Assistant** so you can iterate in seconds instead of restarting HA every time. It hits real Caruna servers with your real credentials but never involves HA.

```bash
cd /path/to/ha-caruna-plus-integration

python3 -m venv .venv
source .venv/bin/activate
pip install aiohttp beautifulsoup4

export CARUNA_USERNAME="your.email@example.com"
export CARUNA_PASSWORD='your-password'   # single quotes preserve special chars

python3 scripts/smoke_login.py
```

Expected output on success:

```
[step 1/7] POST /api/authorization/login             OK
[step 2/7] follow loginRedirectUrl                   OK  (→ authentication2.caruna.fi/…)
[step 3/7] fetch Wicket login form                   OK  (12 hidden fields)
[step 4/7] POST credentials                          OK  (no MFA challenge detected)
[step 5/7] follow post-credentials redirect          OK
[step 6/7] fetch final auto-submit form              OK
[step 7/7] POST token exchange                       OK
Access token: eyJ...<redacted>
Token expires at: 2026-08-13T15:47:22+00:00
Customer numbers: ['12345678']
```

**If it fails**, the script:
- Names the step that failed.
- Dumps the response body (or a snippet) into `/tmp/caruna_smoke_<step>.html`.
- Prints the file path.

**Send me:**
1. The console output up to and including the failure line.
2. The first 100 lines of the dumped HTML (`head -100 /tmp/caruna_smoke_stepN.html`).
3. Redact your username/password from anything you paste — the script tries to but double-check.

---

## 4. Phase B — capture a HAR (do this second)

Do this once, ideally in parallel with Phase A. It gives us the reference we need to fill in the contract/invoice/price endpoints (`TODO(har)` in `const.py`).

1. Open Firefox or Chrome, incognito / private window (clean cookie state).
2. F12 → **Network** tab.
3. Check **Preserve log** (Chrome) or **Persist Logs** (Firefox) so redirects aren't cleared.
4. Optionally check **Disable cache** to force full requests.
5. Navigate to `https://plus.caruna.fi/` and log in normally.
6. Once logged in, click around: dashboard, hourly consumption view, contract page, invoices list, an invoice detail. Each click is a page we want the endpoint for.
7. Right-click anywhere in the Network table → **Save all as HAR with content**. Save it locally.

**Do not commit the HAR to the repo** — it contains your bearer token, cookies, and personal data. The repo's `.gitignore` already excludes `docs/har/`. Put your file there for your own reference and, when sharing with me, **redact first**:

```bash
# Quick redaction — replaces your customer numbers, tokens, addresses.
# Adjust the sed patterns to match your actual values.
sed -i.bak \
  -e 's/12345678/CUSTOMER_XXX/g' \
  -e 's/eyJ[A-Za-z0-9._-]*/TOKEN_XXX/g' \
  -e 's/your\.email@example\.com/USER_XXX/g' \
  docs/har/caruna-login.har
```

Then paste **just the URLs and response schemas** (not full bodies) for:
- The invoices list endpoint.
- One invoice detail endpoint.
- The prices / tariff endpoint if it exists.

That's all I need to update `const.py` and swap the placeholders for real paths.

---

## 5. Phase C — install into your test HA

Only do this once Phase A succeeds. There's no point installing a broken auth flow into HA.

### 5a. Copy the integration

Pick whichever method matches how you access the HA host:

**Samba / SSH:**
```bash
# Assuming your HA config dir is /config
rsync -av --delete \
  custom_components/caruna_plus/ \
  root@192.168.1.100:/config/custom_components/caruna_plus/
```

**Manual:** copy the entire `custom_components/caruna_plus/` folder from this repo into `<ha-config>/custom_components/` on the HA host.

### 5b. Turn on debug logging

Before starting, edit `<ha-config>/configuration.yaml` and add:

```yaml
logger:
  default: warning
  logs:
    custom_components.caruna_plus: debug
    custom_components.caruna_plus.auth: debug
```

Restart Home Assistant: **Settings → System → Restart**.

### 5c. Add the integration

1. Open http://192.168.1.100:8123/config/integrations.
2. **Add integration** → search "Caruna+" → select.
3. Enter your username + password.
4. Expected: form dismisses, integration appears, sensors populate within 30–60 seconds.

### 5d. Verify

Open **Settings → Devices & Services → Caruna+ → Configure** and check:

- The metering-point device shows the address as its name.
- Consumption sensors have values (or at worst `unknown` if today has no readings yet — check `last_reading_time`).
- Contract sensors (fuse, tariff) have values.
- Cost sensors either have values or `unknown` (`unknown` is expected if the price endpoint is still a `TODO(har)` placeholder).
- Billing sensors have values (or `unknown` if invoice endpoint is a placeholder).

### 5e. If it fails in HA specifically

Grab the log excerpt:

**Settings → System → Logs → Load Full Logs**, then search for `caruna_plus` and copy from the first `caruna_plus` line through the traceback / error.

Also grab a diagnostics dump:

**Settings → Devices & Services → Caruna+ → ⋮ (three-dot menu) → Download diagnostics**. This produces a redacted JSON file — safe to paste.

Send both back.

---

## 6. Iteration loop

When I ship a fix:

1. Pull the changes (or I paste the diff).
2. `rsync` the `custom_components/caruna_plus/` directory to `/config/custom_components/caruna_plus/` again.
3. **Settings → Devices & Services → Caruna+ → ⋮ → Reload** (no HA restart needed for most changes).
4. If auth code changed and you want to force a fresh login: **⋮ → Delete**, then re-add.

For standalone script iterations, just re-run `python3 scripts/smoke_login.py`.

---

## 7. Known-fragile spots — where breakage is most likely

Ranked by my expectation of "this is what will break first":

1. **Wicket component path** (`0-userIDPanel-usernameLogin-loginWithUserID` in `const.py`). If Caruna changed the form component naming, credentials will 401 or the AJAX response won't contain the expected CDATA URL. Fix: replace with whatever's in the HAR's step-4 request URL.
2. **MFA detection heuristic** (`_looks_like_mfa_challenge` in `auth.py`). It looks for keywords like `"verification code"` and `"vahvistuskoodi"`. If Caruna uses different wording (e.g. `"turvakoodi"`), a real MFA challenge slips through as a login success. Fix: add the real keyword we see in your HAR.
3. **Token expiry field name** (`expiresIn` vs `expires_in` in `auth.py`). Guessed from convention. If the real name is `ttl` or something else, tokens will look permanently expired and re-login on every call.
4. **Assets response shape** (`assetId` vs `meteringPointId` in `api.py`). We accept several field names but not all.
5. **Invoices / prices / contract endpoints** — placeholders, will 404. Sensors return `unknown` gracefully.

Every one of these is a small localised patch once we see the real HAR.
