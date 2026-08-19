"""Shared fixtures."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

# Patch aioresponses for aiohttp 3.11+ compatibility.
# aiohttp 3.11+ added `stream_writer` as a required kwarg to ClientResponse.
# aioresponses 0.7.x doesn't pass it, so we swap in a subclass that defaults it.
try:
    import aiohttp as _aiohttp
    import aioresponses.core as _aio_core
    from unittest.mock import Mock as _Mock

    class _PatchedClientResponse(_aiohttp.ClientResponse):  # type: ignore[misc]
        def __init__(self, method, url, *, stream_writer=None, **kwargs):  # type: ignore[no-untyped-def]
            super().__init__(method, url, stream_writer=stream_writer or _Mock(), **kwargs)

    _aio_core.ClientResponse = _PatchedClientResponse  # type: ignore[attr-defined]
except (ImportError, AttributeError):
    pass

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for every test in this package."""
    yield


@pytest.fixture
def mock_config_entry():
    """Return a MockConfigEntry pre-populated for caruna_plus tests."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.caruna_plus.const import DOMAIN

    return MockConfigEntry(
        domain=DOMAIN,
        data={"username": "u@test.fi", "password": "secret", "customer": "12345678"},
        unique_id="12345678",
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def wicket_landing_html() -> str:
    """First redirect target — meta refresh into the IDP."""
    return """
<html>
<head>
<meta http-equiv="refresh" content="0;URL=https://authentication2.caruna.fi/portal/idp?flow=1"/>
</head>
<body></body>
</html>
"""


@pytest.fixture
def wicket_login_form_html() -> str:
    """IDP page with the Wicket form we scrape."""
    return """
<html>
<body>
<form action="./portal;jsessionid=ABC?0-1.IBehaviorListener.0-userIDPanel-usernameLogin" method="post">
  <input type="hidden" name="anti_csrf" value="csrftoken-xyz" />
  <input type="hidden" name="hf-0" value="" />
  <input type="text" name="ttqusername" />
  <input type="password" name="userPassword" />
  <button name="1" type="submit">Login</button>
</form>
</body>
</html>
"""


@pytest.fixture
def wicket_ajax_success_body() -> str:
    """AJAX response after credentials — CDATA'd redirect URL."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<ajax-response>
  <redirect><![CDATA[https://authentication2.caruna.fi/portal/idp?flow=1&step=complete]]></redirect>
</ajax-response>
"""


@pytest.fixture
def wicket_ajax_mfa_body() -> str:
    """AJAX response that indicates an MFA challenge (heuristic)."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<ajax-response>
  <component id="mfa"><![CDATA[<div>Enter verification code (SMS) </div>]]></component>
</ajax-response>
"""


@pytest.fixture
def wicket_meta_refresh_html() -> str:
    return """
<html><head>
<meta http-equiv="refresh" content="0;URL=https://plus.caruna.fi/api/authorization/callback?code=XYZ&state=abc"/>
</head></html>
"""


@pytest.fixture
def wicket_final_form_html() -> str:
    return """
<html><body>
<form action="https://plus.caruna.fi/api/authorization/callback" method="post">
  <input type="hidden" name="code" value="XYZ"/>
  <input type="hidden" name="state" value="abc"/>
</form>
</body></html>
"""


@pytest.fixture
def token_response_json() -> dict:
    return {
        "token": "eyJhbGciOi.stub.token",
        "expiresIn": 3600,
        "user": {
            "ownCustomerNumbers": ["12345678"],
            "fullName": "Test User",
        },
    }


@pytest.fixture
def assets_response_json() -> list[dict]:
    return [
        {
            "assetId": "MP-1",
            "meterId": "MTR-9999",
            "mainFuseSize": "3x25A",
            "contractType": "Yleissähkö",
            "tariff": "Yleissiirto",
            "basicFee": 4.5,
            "deliveryAddress": {
                "street": "Testikatu 1",
                "postalCode": "00100",
                "city": "Helsinki",
            },
        }
    ]


@pytest.fixture
def energy_response_json() -> dict:
    return {
        "values": [
            {"timestamp": "2026-08-12T00:00:00+00:00", "value": 1.234},
            {"timestamp": "2026-08-12T01:00:00+00:00", "value": 0.987},
        ]
    }


@pytest.fixture
def invoices_response_json() -> list[dict]:
    return [
        {
            "invoiceId": "INV-1",
            "amount": 120.5,
            "currency": "EUR",
            "dueDate": "2026-08-25",
            "issuedDate": "2026-08-01",
            "status": "open",
        },
        {
            "invoiceId": "INV-0",
            "amount": 95.0,
            "currency": "EUR",
            "dueDate": "2026-07-25",
            "issuedDate": "2026-07-01",
            "status": "paid",
        },
    ]


@pytest.fixture
def energy_response_with_costs() -> dict:
    """Energy response using confirmed field names with optional cost fields."""
    return {
        "values": [
            {
                "timestamp": "2026-08-12T00:00:00+00:00",
                "totalConsumption": 2.5,
                "totalFee": 0.80,
                "distributionFee": 0.30,
                "electricityTax": 0.10,
                "valueAddedTax": 0.20,
            },
            {
                "timestamp": "2026-08-12T01:00:00+00:00",
                "totalConsumption": 1.8,
                "totalFee": 0.58,
                "distributionFee": 0.21,
                "electricityTax": 0.07,
                "valueAddedTax": 0.14,
            },
        ]
    }


@pytest.fixture
def invoices_open_response() -> list[dict]:
    return [
        {
            "invoiceId": "INV-OPEN",
            "amount": 120.5,
            "currency": "EUR",
            "dueDate": "2026-08-25",
            "issuedDate": "2026-08-01",
            "status": "open",
        }
    ]


@pytest.fixture
def invoices_paid_response() -> list[dict]:
    return [
        {
            "invoiceId": "INV-PAID",
            "amount": 95.0,
            "currency": "EUR",
            "dueDate": "2026-07-25",
            "issuedDate": "2026-07-01",
            "status": "paid",
        }
    ]
