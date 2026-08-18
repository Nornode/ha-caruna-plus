"""Constants for the Caruna+ integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "caruna_plus"

# Config entry keys
CONF_CUSTOMER = "customer"
CONF_ENABLE_HOURLY = "enable_hourly"
CONF_UPDATE_INTERVAL = "update_interval_minutes"

# Config entry stored data
DATA_TOKEN = "token"
DATA_TOKEN_EXPIRES = "token_expires_at"
DATA_CUSTOMERS = "customers"

# Defaults
DEFAULT_UPDATE_INTERVAL_MINUTES = 60
MIN_UPDATE_INTERVAL_MINUTES = 15

# Fetch cadences for coordinator sub-fetchers
CONTRACT_FETCH_INTERVAL = timedelta(hours=24)
BILLING_FETCH_INTERVAL = timedelta(hours=6)

# LTS
LTS_SOURCE = DOMAIN
LTS_STATISTIC_ID_TEMPLATE = f"{DOMAIN}:{{mp}}_energy"
LTS_BACKFILL_DAYS = 30

# API endpoints — confirmed from probe (2026-08-18).
# Energy: timespan=daily returns 24 hourly rows; timespan=hourly → 400.
# Invoices: ?status=open|paid; returns compressedInvoices columnar format.
#   Field names: id, amount, dueDate, invoiceDate, isOverdue, vatAmount, vatExcluded, amountOpen.
# Prices: all candidate paths 404 — use _derive_price_from_invoices fallback.
BASE_URL = "https://plus.caruna.fi"
AUTH_BASE_URL = "https://authentication2.caruna.fi"

EP_LOGIN = f"{BASE_URL}/api/authorization/login"
EP_TOKEN = f"{BASE_URL}/api/authorization/token"
EP_ASSETS = f"{BASE_URL}/api/customers/{{customer}}/assets"
EP_ENERGY = f"{BASE_URL}/api/customers/{{customer}}/assets/{{mp}}/energy"
# Confirmed: requires ?status=open or ?status=paid; returns compressedInvoices.
EP_INVOICES = f"{BASE_URL}/api/customers/{{customer}}/invoices"
# TODO(har): invoice detail path unconfirmed — may not exist.
EP_INVOICE = f"{BASE_URL}/api/customers/{{customer}}/invoices/{{invoice_id}}"
# No prices endpoint — all candidate paths return 404.
EP_PRICES = f"{BASE_URL}/api/customers/{{customer}}/assets/{{mp}}/prices"

# Auth flow tuning
TOKEN_REFRESH_MARGIN_SECONDS = 300  # refresh 5 min before expiry
LOGIN_TIMEOUT_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 30

# Wicket / IDP form field names
WICKET_USERNAME_FIELD = "ttqusername"
WICKET_PASSWORD_FIELD = "userPassword"
WICKET_LOGIN_BUTTON = "1"
WICKET_COMPONENT_PATH = "0-userIDPanel-usernameLogin-loginWithUserID"
WICKET_FOCUS_ELEMENT = "loginWithUserID5"
