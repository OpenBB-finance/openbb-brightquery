"""
BrightQuery KYB - OpenBB Workspace App
Know Your Business data from BrightQuery Append API

Uses @register_widget decorator to keep widget metadata next to endpoint code.
Watchlist pattern: search returns matches, click a row to sync all detail widgets via bq_id.
"""

import json
import time
import asyncio
import hashlib
from functools import wraps
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="BrightQuery KYB",
    description="Know Your Business data from BrightQuery",
    version="1.0.0",
)

# CORS configuration for OpenBB Workspace
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://pro.openbb.co",
        "https://pro.openbb.dev",
        "https://excel.openbb.co",
        "https://excel.openbb.dev",
        "http://localhost:7780",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# BrightQuery API endpoints
BQ_ORG_URL = "https://apigateway.brightquery.com/auth/bq-append/org"
BQ_EXEC_URL = "https://apigateway.brightquery.com/auth/bq-append/executives"


# Cache for API responses (keyed by search params hash, TTL=120s)
_cache = {}
CACHE_TTL = 120

# Store last search results so detail widgets can look up by bq_id
_last_search_results = {}

# Widget registry
WIDGETS = {}


def register_widget(widget_config):
    """
    Decorator that registers a widget configuration.
    Keeps widget metadata next to endpoint code for better maintainability.
    """

    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        endpoint = widget_config.get("endpoint")
        if endpoint:
            if "widgetId" not in widget_config:
                widget_config["widgetId"] = endpoint
            widget_id = widget_config["widgetId"]
            WIDGETS[widget_id] = widget_config

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def _cache_key(prefix: str, params: dict) -> str:
    """Generate a cache key from prefix and non-empty params."""
    filtered = {k: v for k, v in sorted(params.items()) if v}
    raw = f"{prefix}:{json.dumps(filtered)}"
    return hashlib.md5(raw.encode()).hexdigest()


async def _fetch_company(auth: tuple[str, str], **search_params) -> list:
    """Fetch company data from BrightQuery org API with caching."""
    params = {k: v for k, v in search_params.items() if v}
    if not params:
        return []

    key = _cache_key("org", params)
    if key in _cache:
        cached_time, cached_data = _cache[key]
        if time.time() - cached_time < CACHE_TTL:
            return cached_data

    async with httpx.AsyncClient() as client:
        response = await client.post(
            BQ_ORG_URL,
            json=params,
            auth=auth,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

    children = data.get("root", {}).get("children", [])
    _cache[key] = (time.time(), children)
    return children


async def _fetch_executives(auth: tuple[str, str], **search_params) -> list:
    """Fetch executive data from BrightQuery executives API with caching."""
    params = {k: v for k, v in search_params.items() if v}
    if not params:
        return []

    key = _cache_key("exec", params)
    if key in _cache:
        cached_time, cached_data = _cache[key]
        if time.time() - cached_time < CACHE_TTL:
            return cached_data

    async with httpx.AsyncClient() as client:
        response = await client.post(
            BQ_EXEC_URL,
            json=params,
            auth=auth,
            timeout=30.0,
        )
        if response.status_code == 204 or not response.content:
            _cache[key] = (time.time(), [])
            return []
        response.raise_for_status()
        data = response.json()

    children = data.get("root", {}).get("children", [])
    _cache[key] = (time.time(), children)
    return children


def _get_record_by_bq_id(bq_id: str) -> dict | None:
    """Look up a cached record by bq_organization_id."""
    return _last_search_results.get(bq_id)


def _format_currency(value) -> str:
    """Format large numbers as currency strings (e.g., 5103487000 -> '$5.1B')."""
    if value is None:
        return "N/A"
    try:
        value = float(value)
    except (ValueError, TypeError):
        return "N/A"

    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    elif abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    elif abs(value) >= 1_000:
        return f"${value / 1_000:.1f}K"
    else:
        return f"${value:,.0f}"


def _format_number(value) -> str:
    """Format a number with commas."""
    if value is None:
        return "N/A"
    try:
        return f"{int(float(value)):,}"
    except (ValueError, TypeError):
        return "N/A"


def _format_percent(value) -> str:
    """Format a decimal as percentage string."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value) * 100:.1f}%"
    except (ValueError, TypeError):
        return "N/A"


def _get_field(record: dict, field: str, default="N/A"):
    """Safely get a field from a record."""
    val = record.get(field)
    if val is None or val == "":
        return default
    return val


# --- Core Endpoints ---


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "app": "BrightQuery KYB", "version": "1.0.0"}


@app.get("/widgets.json")
async def get_widgets():
    """Serve widgets configuration from registry."""
    return WIDGETS


@app.get("/apps.json")
async def get_apps():
    """Serve apps/dashboard configuration."""
    apps_path = Path(__file__).parent / "apps.json"
    with open(apps_path) as f:
        return json.load(f)


# --- Tab 1: Company Search (search widget with clickable rows) ---

SEARCH_PARAMS = [
    {
        "paramName": "company_name",
        "label": "Company Name",
        "description": "Name of the company to search",
        "type": "text",
        "value": "",
    },
    {
        "paramName": "ticker",
        "label": "Ticker",
        "description": "Stock ticker symbol",
        "type": "text",
        "value": "",
    },
    {
        "paramName": "website",
        "label": "Website",
        "description": "Company website URL",
        "type": "text",
        "value": "",
    },
    {
        "paramName": "ein",
        "label": "EIN",
        "description": "Employer Identification Number",
        "type": "text",
        "value": "",
    },
    {
        "paramName": "address",
        "label": "Address",
        "description": "Company address",
        "type": "text",
        "value": "",
    },
    {
        "paramName": "cik",
        "label": "CIK",
        "description": "SEC Central Index Key",
        "type": "text",
        "value": "",
    },
    {
        "paramName": "bq_id",
        "label": "BQ ID",
        "description": "BrightQuery unique identifier",
        "type": "text",
        "value": "",
        "show": False,
    },
]


@register_widget(
    {
        "name": "Company Search",
        "description": "Search for companies and click a row to view details",
        "type": "table",
        "endpoint": "company_search",
        "gridData": {"w": 40, "h": 10},
        "source": "BrightQuery",
        "params": SEARCH_PARAMS,
        "data": {
            "table": {
                "showAll": True,
                "columnsDefs": [
                    {
                        "field": "bq_id",
                        "headerName": "BQ ID",
                        "cellDataType": "text",
                        "width": 160,
                        "pinned": "left",
                        "renderFn": "cellOnClick",
                        "renderFnParams": {
                            "actionType": "groupBy",
                            "groupByParamName": "bq_id",
                        },
                    },
                    {
                        "field": "name",
                        "headerName": "Name",
                        "width": 200,
                        "cellDataType": "text",
                    },
                    {
                        "field": "ticker",
                        "headerName": "Ticker",
                        "width": 90,
                        "cellDataType": "text",
                    },
                    {
                        "field": "website",
                        "headerName": "Website",
                        "width": 180,
                        "cellDataType": "text",
                    },
                    {
                        "field": "ein",
                        "headerName": "EIN",
                        "width": 110,
                        "cellDataType": "text",
                    },
                    {
                        "field": "cik",
                        "headerName": "CIK",
                        "width": 120,
                        "cellDataType": "text",
                    },
                    {
                        "field": "type",
                        "headerName": "Type",
                        "width": 120,
                        "cellDataType": "text",
                    },
                    {
                        "field": "industry",
                        "headerName": "Industry",
                        "width": 200,
                        "cellDataType": "text",
                    },
                    {
                        "field": "city",
                        "headerName": "City",
                        "width": 120,
                        "cellDataType": "text",
                    },
                    {
                        "field": "state",
                        "headerName": "State",
                        "width": 80,
                        "cellDataType": "text",
                    },
                    {
                        "field": "revenue",
                        "headerName": "Revenue",
                        "width": 120,
                        "cellDataType": "text",
                    },
                    {
                        "field": "employees",
                        "headerName": "Employees",
                        "width": 100,
                        "cellDataType": "text",
                    },
                    {
                        "field": "confidence",
                        "headerName": "Confidence",
                        "width": 100,
                        "cellDataType": "number",
                    },
                ],
            },
        },
    }
)
@app.get("/company_search")
async def company_search(
    company_name: str = Query("", description="Company name"),
    ticker: str = Query("", description="Ticker symbol"),
    website: str = Query("", description="Website URL"),
    ein: str = Query("", description="EIN"),
    address: str = Query("", description="Address"),
    cik: str = Query("", description="CIK"),
    bq_id: str = Query("", description="BQ ID"),
    x_bq_username: str = Header(...),
    x_bq_password: str = Header(...),
):
    """Search for companies. Click a row to sync detail widgets."""
    auth = (x_bq_username, x_bq_password)
    children = await _fetch_company(
        auth,
        company_name=company_name,
        ticker=ticker,
        website=website,
        ein=ein,
        address=address,
        cik=cik,
        bq_id=bq_id,
    )
    if not children:
        return []

    # Store results so detail widgets can look up by bq_organization_id
    for rec in children:
        rid = "BQ_" + str(rec.get("bq_organization_id", ""))
        if rid != "BQ_":
            _last_search_results[rid] = rec

    return [
        {
            "bq_id": "BQ_" + str(_get_field(rec, "bq_organization_id")),
            "name": _get_field(rec, "bq_organization_name"),
            "ticker": _get_field(rec, "bq_organization_ticker"),
            "website": _get_field(rec, "bq_organization_website"),
            "ein": _get_field(rec, "bq_organization_ein"),
            "cik": _get_field(rec, "bq_organization_cik"),
            "type": _get_field(rec, "bq_organization_company_type"),
            "industry": _get_field(rec, "bq_organization_naics_name"),
            "city": _get_field(rec, "bq_organization_address1_city"),
            "state": _get_field(rec, "bq_organization_address1_state"),
            "revenue": _format_currency(rec.get("bq_revenue_mr")),
            "employees": _format_number(rec.get("bq_employment_mr")),
            "confidence": rec.get("bq_confidence_score", 0),
        }
        for rec in children
    ]


# --- Detail widgets (all driven by bq_id from group click) ---

DETAIL_PARAM = [
    {
        "paramName": "bq_id",
        "label": "BQ ID",
        "description": "Company BQ ID (e.g. BQ_100000671067)",
        "type": "text",
        "value": "",
    },
]


async def _get_detail_record(bq_id: str, auth: tuple[str, str]) -> dict | None:
    """Get a company record by bq_id, from cache or fresh API call."""
    rec = _get_record_by_bq_id(bq_id)
    if rec:
        return rec

    api_id = bq_id.removeprefix("BQ_") if bq_id else ""
    if api_id:
        children = await _fetch_company(auth, bq_id=api_id)
        if children:
            for c in children:
                rid = "BQ_" + str(c.get("bq_organization_id", ""))
                if rid != "BQ_":
                    _last_search_results[rid] = c
            return children[0]
    return None


@register_widget(
    {
        "name": "Company Profile",
        "description": "Firmographic details, identifiers, and classification",
        "type": "table",
        "endpoint": "company_profile",
        "gridData": {"w": 20, "h": 15},
        "source": "BrightQuery",
        "params": DETAIL_PARAM,
        "data": {
            "columnsDefs": [
                {
                    "field": "field",
                    "headerName": "Field",
                    "width": 180,
                    "cellDataType": "text",
                },
                {
                    "field": "value",
                    "headerName": "Value",
                    "width": 300,
                    "cellDataType": "text",
                },
            ]
        },
    }
)
@app.get("/company_profile")
async def company_profile(
    bq_id: str = Query("", description="BQ ID"),
    x_bq_username: str = Header(...),
    x_bq_password: str = Header(...),
):
    """Firmographic profile for the selected company."""
    auth = (x_bq_username, x_bq_password)
    rec = await _get_detail_record(bq_id, auth)
    if not rec:
        return []

    fields = [
        ("Organization Name", "bq_organization_name"),
        ("Legal Name", "bq_organization_legal_name"),
        ("Company Type", "bq_organization_company_type"),
        ("Legal Form", "bq_organization_lfo"),
        ("Structure", "bq_organization_structure"),
        ("Year Founded", "bq_organization_year_founded"),
        ("Active Status", "bq_organization_active_indicator"),
        ("Public/Private", "bq_organization_public_indicator"),
        ("NAICS Industry", "bq_organization_naics_name"),
        ("NAICS Sector", "bq_organization_naics_sector_name"),
        ("IRS Industry", "bq_organization_irs_industry_name"),
        ("Website", "bq_organization_website"),
        ("Ticker", "bq_organization_ticker"),
        ("EIN", "bq_organization_ein"),
        ("CIK", "bq_organization_cik"),
        ("LEI", "bq_organization_lei"),
        ("LinkedIn", "bq_organization_linkedin_url"),
        ("Crunchbase", "bq_organization_crunchbase_url"),
        ("Address", "bq_organization_address_summary"),
        ("Phone", "bq_organization_phone_formatted"),
    ]

    return [
        {"field": label, "value": str(_get_field(rec, key))} for label, key in fields
    ]


@register_widget(
    {
        "name": "Risk & Compliance",
        "description": "Credit score, OFAC, tax liens, and compliance indicators",
        "type": "table",
        "endpoint": "risk_compliance",
        "gridData": {"w": 20, "h": 15},
        "source": "BrightQuery",
        "params": DETAIL_PARAM,
        "data": {
            "columnsDefs": [
                {
                    "field": "field",
                    "headerName": "Indicator",
                    "width": 200,
                    "cellDataType": "text",
                },
                {
                    "field": "value",
                    "headerName": "Value",
                    "width": 280,
                    "cellDataType": "text",
                },
            ]
        },
    }
)
@app.get("/risk_compliance")
async def risk_compliance(
    bq_id: str = Query("", description="BQ ID"),
    x_bq_username: str = Header(...),
    x_bq_password: str = Header(...),
):
    """Risk and compliance indicators for the selected company."""
    auth = (x_bq_username, x_bq_password)
    rec = await _get_detail_record(bq_id, auth)
    if not rec:
        return []

    fields = [
        ("Credit Score", "bq_credit_score"),
        ("OFAC Sanctioned", "bq_organization_ofac_indicator"),
        ("IRS Tax Lien", "bq_organization_irs_taxlien_indicator"),
        ("Legal Entity Tax Lien", "bq_legal_entity_irs_taxlien_indicator"),
        ("Nonprofit", "bq_organization_nonprofit_indicator"),
        ("Active Status", "bq_organization_active_indicator"),
        ("Legal Entity Status", "bq_legal_entity_current_status"),
        ("S&P 500", "bq_sp500_indicator"),
        ("Public Indicator", "bq_public_indicator"),
        ("Confidence Score", "bq_confidence_score"),
        ("Match Types", "bq_match_types"),
    ]

    return [
        {"field": label, "value": str(_get_field(rec, key))} for label, key in fields
    ]


@register_widget(
    {
        "name": "Financials & Growth",
        "description": "Revenue, margins, growth rates, and per-employee efficiency",
        "type": "table",
        "endpoint": "financials_growth",
        "gridData": {"w": 20, "h": 17},
        "source": "BrightQuery",
        "params": DETAIL_PARAM,
        "data": {
            "columnsDefs": [
                {
                    "field": "field",
                    "headerName": "Metric",
                    "width": 200,
                    "cellDataType": "text",
                },
                {
                    "field": "value",
                    "headerName": "Value",
                    "width": 280,
                    "cellDataType": "text",
                },
            ]
        },
    }
)
@app.get("/financials_growth")
async def financials_growth(
    bq_id: str = Query("", description="BQ ID"),
    x_bq_username: str = Header(...),
    x_bq_password: str = Header(...),
):
    """Financial metrics and growth for the selected company."""
    auth = (x_bq_username, x_bq_password)
    rec = await _get_detail_record(bq_id, auth)
    if not rec:
        return []

    rows = [
        ("Revenue", _format_currency(rec.get("bq_revenue_mr"))),
        ("EBITDA", _format_currency(rec.get("bq_ebitda_mr"))),
        ("Net Income", _format_currency(rec.get("bq_net_income_mr"))),
        ("Gross Profit", _format_currency(rec.get("bq_gross_profit_mr"))),
        ("Operating Income", _format_currency(rec.get("bq_operating_income_mr"))),
        ("Total Assets", _format_currency(rec.get("bq_total_assets_mr"))),
        ("Cost of Revenue", _format_currency(rec.get("bq_cor_mr"))),
        ("Payroll", _format_currency(rec.get("bq_payroll_mr"))),
        ("Employees", _format_number(rec.get("bq_employment_mr"))),
        ("Revenue Growth YoY", _format_percent(rec.get("bq_revenue_growth_yoy_mr"))),
        ("Revenue Growth QoQ", _format_percent(rec.get("bq_revenue_growth_qoq_mr"))),
        (
            "Revenue Growth Quarterly YoY",
            _format_percent(rec.get("bq_revenue_growth_quarterly_yoy_mr")),
        ),
        ("Gross Profit Margin", _format_percent(rec.get("bq_gross_profit_margin_mr"))),
        ("EBITDA Margin", _format_percent(rec.get("bq_ebitda_margin_mr"))),
        ("Return on Sales", _format_percent(rec.get("bq_return_on_sales_mr"))),
        ("Asset Turnover", str(_get_field(rec, "bq_asset_turnover_mr"))),
        ("Revenue / Employee", _format_currency(rec.get("bq_revenue_mr_per_emp"))),
        ("EBITDA / Employee", _format_currency(rec.get("bq_ebitda_mr_per_emp"))),
        (
            "Gross Profit / Employee",
            _format_currency(rec.get("bq_gross_profit_mr_per_emp")),
        ),
        (
            "Operating Income / Employee",
            _format_currency(rec.get("bq_operating_income_mr_per_emp")),
        ),
        ("Cost of Revenue / Employee", _format_currency(rec.get("bq_cor_mr_per_emp"))),
        ("Payroll / Employee", _format_currency(rec.get("bq_payroll_mr_per_emp"))),
        ("Report Date", str(_get_field(rec, "bq_report_date_mr"))),
    ]

    return [{"field": label, "value": value} for label, value in rows]


@register_widget(
    {
        "name": "Funding",
        "description": "Funding rounds, amounts, and investment stage",
        "type": "table",
        "endpoint": "funding",
        "gridData": {"w": 20, "h": 10},
        "source": "BrightQuery",
        "params": DETAIL_PARAM,
        "data": {
            "columnsDefs": [
                {
                    "field": "field",
                    "headerName": "Field",
                    "width": 200,
                    "cellDataType": "text",
                },
                {
                    "field": "value",
                    "headerName": "Value",
                    "width": 280,
                    "cellDataType": "text",
                },
            ]
        },
    }
)
@app.get("/funding")
async def funding(
    bq_id: str = Query("", description="BQ ID"),
    x_bq_username: str = Header(...),
    x_bq_password: str = Header(...),
):
    """Funding data for the selected company."""
    auth = (x_bq_username, x_bq_password)
    rec = await _get_detail_record(bq_id, auth)
    if not rec:
        return []

    fields = [
        ("Total Funding", _format_currency(rec.get("bq_funding_amount_total"))),
        ("Latest Round Amount", _format_currency(rec.get("bq_funding_amount_latest"))),
        ("Latest Round Date", str(_get_field(rec, "bq_funding_date_latest"))),
        ("Latest Stage", str(_get_field(rec, "bq_funding_stage_latest"))),
        ("Total Rounds", str(_get_field(rec, "bq_funding_rounds_total"))),
    ]

    return [{"field": label, "value": value} for label, value in fields]


@register_widget(
    {
        "name": "Corporate Structure",
        "description": "Legal entity hierarchy, parent/subsidiary relationships",
        "type": "table",
        "endpoint": "corporate_structure",
        "gridData": {"w": 20, "h": 10},
        "source": "BrightQuery",
        "params": DETAIL_PARAM,
        "data": {
            "columnsDefs": [
                {
                    "field": "field",
                    "headerName": "Field",
                    "width": 200,
                    "cellDataType": "text",
                },
                {
                    "field": "value",
                    "headerName": "Value",
                    "width": 280,
                    "cellDataType": "text",
                },
            ]
        },
    }
)
@app.get("/corporate_structure")
async def corporate_structure(
    bq_id: str = Query("", description="BQ ID"),
    x_bq_username: str = Header(...),
    x_bq_password: str = Header(...),
):
    """Corporate structure and legal entity tree for the selected company."""
    auth = (x_bq_username, x_bq_password)
    rec = await _get_detail_record(bq_id, auth)
    if not rec:
        return []

    fields = [
        ("Legal Entity Name", "bq_legal_entity_name"),
        ("Legal Entity ID", "bq_legal_entity_id"),
        ("Parent Status", "bq_legal_entity_parent_status"),
        ("Jurisdiction", "bq_legal_entity_jurisdiction_code"),
        ("Entity Status", "bq_legal_entity_current_status"),
        ("Active Indicator", "bq_legal_entity_active_indicator"),
        ("Subsidiaries Count", "bq_legal_entity_children_count"),
        ("Subsidiary IDs", "bq_legal_entity_children_ids"),
        ("Immediate Children", "bq_legal_entity_immediate_children_count"),
        ("Establishments", "bq_legal_entity_immediate_establishment_count"),
        ("Registered Address", "bq_legal_entity_address_summary"),
    ]

    return [
        {"field": label, "value": str(_get_field(rec, key))} for label, key in fields
    ]


# --- Tab 2: People Lookup (search + clickable detail) ---

# Store last exec search results for detail lookup
_last_exec_results = {}

EXEC_SEARCH_PARAMS = [
    {
        "paramName": "executives_name",
        "label": "Executive Name",
        "description": "Name of the executive to search",
        "type": "text",
        "value": "",
    },
    {
        "paramName": "linkedin_url",
        "label": "LinkedIn URL",
        "description": "Executive LinkedIn profile URL",
        "type": "text",
        "value": "",
    },
    {
        "paramName": "email",
        "label": "Email",
        "description": "Executive email address",
        "type": "text",
        "value": "",
    },
    {
        "paramName": "exec_id",
        "label": "Executive ID",
        "description": "Selected executive ID",
        "type": "text",
        "value": "",
        "show": False,
    },
]


EXEC_DETAIL_PARAM = [
    {
        "paramName": "exec_id",
        "label": "Executive ID",
        "description": "Executive ID (e.g. EX_8880267630930)",
        "type": "text",
        "value": "",
    },
]


def _format_exec_row(rec: dict) -> dict:
    """Format an executive record for the search results table."""
    return {
        "exec_id": "EX_" + str(_get_field(rec, "bq_executive_id")),
        "name": _get_field(rec, "bq_executive_name"),
        "title": _get_field(rec, "bq_executive_highest_title"),
        "company": _get_field(rec, "bq_organization_name"),
        "signatory": _get_field(rec, "bq_executive_signatory_indicator"),
        "industry": _get_field(rec, "bq_organization_naics_name"),
        "company_revenue": _format_currency(rec.get("bq_revenue_mr")),
        "employees": _format_number(rec.get("bq_employment_mr")),
        "email": _get_field(rec, "bq_executive_emails"),
        "phone": _get_field(rec, "bq_executive_landlines_direct_formatted"),
        "linkedin": _get_field(rec, "bq_executive_linkedin_url"),
        "city": _get_field(rec, "bq_executive_address1_city"),
        "state": _get_field(rec, "bq_executive_address1_state"),
    }


@register_widget(
    {
        "name": "Executive Search",
        "description": "Search for executives and click a row to view full profile",
        "type": "table",
        "endpoint": "executive_search",
        "gridData": {"w": 40, "h": 12},
        "source": "BrightQuery",
        "params": EXEC_SEARCH_PARAMS,
        "data": {
            "table": {
                "showAll": True,
                "columnsDefs": [
                    {
                        "field": "exec_id",
                        "headerName": "EX ID",
                        "cellDataType": "text",
                        "width": 160,
                        "pinned": "left",
                        "renderFn": "cellOnClick",
                        "renderFnParams": {
                            "actionType": "groupBy",
                            "groupByParamName": "exec_id",
                        },
                    },
                    {
                        "field": "name",
                        "headerName": "Name",
                        "width": 180,
                        "cellDataType": "text",
                    },
                    {
                        "field": "title",
                        "headerName": "Title",
                        "width": 180,
                        "cellDataType": "text",
                    },
                    {
                        "field": "company",
                        "headerName": "Company",
                        "width": 200,
                        "cellDataType": "text",
                    },
                    {
                        "field": "signatory",
                        "headerName": "Signatory",
                        "width": 100,
                        "cellDataType": "text",
                    },
                    {
                        "field": "industry",
                        "headerName": "Industry",
                        "width": 180,
                        "cellDataType": "text",
                    },
                    {
                        "field": "company_revenue",
                        "headerName": "Company Revenue",
                        "width": 140,
                        "cellDataType": "text",
                    },
                    {
                        "field": "employees",
                        "headerName": "Employees",
                        "width": 100,
                        "cellDataType": "text",
                    },
                    {
                        "field": "email",
                        "headerName": "Email",
                        "width": 220,
                        "cellDataType": "text",
                    },
                    {
                        "field": "phone",
                        "headerName": "Phone",
                        "width": 150,
                        "cellDataType": "text",
                    },
                    {
                        "field": "linkedin",
                        "headerName": "LinkedIn",
                        "width": 250,
                        "cellDataType": "text",
                    },
                    {
                        "field": "city",
                        "headerName": "City",
                        "width": 120,
                        "cellDataType": "text",
                    },
                    {
                        "field": "state",
                        "headerName": "State",
                        "width": 80,
                        "cellDataType": "text",
                    },
                ],
            },
        },
    }
)
@app.get("/executive_search")
async def executive_search(
    executives_name: str = Query("", description="Executive name"),
    linkedin_url: str = Query("", description="LinkedIn URL"),
    email: str = Query("", description="Email address"),
    exec_id: str = Query("", description="Executive ID"),
    x_bq_username: str = Header(...),
    x_bq_password: str = Header(...),
):
    """Search for executives. Click a row to view full profile."""
    auth = (x_bq_username, x_bq_password)
    children = await _fetch_executives(
        auth,
        executives_name=executives_name,
        linkedin_url=linkedin_url,
        email=email,
    )
    if not children:
        return []

    # Store results for detail widget lookup
    for rec in children:
        eid = "EX_" + str(rec.get("bq_executive_id", ""))
        if eid != "EX_":
            _last_exec_results[eid] = rec

    return [_format_exec_row(rec) for rec in children]


async def _get_exec_detail_record(exec_id: str, auth: tuple[str, str]) -> dict | None:
    """Get an executive record by exec_id, from cache or fresh API call."""
    rec = _last_exec_results.get(exec_id)
    if rec:
        return rec

    api_id = exec_id.removeprefix("EX_") if exec_id else ""
    if api_id:
        children = await _fetch_executives(auth, bq_executive_id=api_id)
        if children:
            for c in children:
                eid = "EX_" + str(c.get("bq_executive_id", ""))
                if eid != "EX_":
                    _last_exec_results[eid] = c
            return children[0]
    return None


@register_widget(
    {
        "name": "Executive Profile",
        "description": "Full profile of the selected executive",
        "type": "table",
        "endpoint": "executive_profile",
        "gridData": {"w": 20, "h": 15},
        "source": "BrightQuery",
        "params": EXEC_DETAIL_PARAM,
        "data": {
            "columnsDefs": [
                {
                    "field": "field",
                    "headerName": "Field",
                    "width": 200,
                    "cellDataType": "text",
                },
                {
                    "field": "value",
                    "headerName": "Value",
                    "width": 300,
                    "cellDataType": "text",
                },
            ]
        },
    }
)
@app.get("/executive_profile")
async def executive_profile(
    exec_id: str = Query("", description="Executive ID"),
    x_bq_username: str = Header(...),
    x_bq_password: str = Header(...),
):
    """Full profile for the selected executive."""
    auth = (x_bq_username, x_bq_password)
    rec = await _get_exec_detail_record(exec_id, auth)
    if not rec:
        return []

    fields = [
        ("Name", "bq_executive_name"),
        ("First Name", "bq_executive_first_name"),
        ("Last Name", "bq_executive_last_name"),
        ("Highest Title", "bq_executive_highest_title"),
        ("All Titles", "bq_executive_titles"),
        ("Signatory", "bq_executive_signatory_indicator"),
        ("Executive ID", "bq_executive_id"),
        ("Email", "bq_executive_emails"),
        ("Phone", "bq_executive_landlines_direct_formatted"),
        ("LinkedIn", "bq_executive_linkedin_url"),
        ("Address", "bq_executive_address1_city"),
        ("State", "bq_executive_address1_state_name"),
        ("County", "bq_executive_address1_county_name"),
        ("Metro Area (CBSA)", "bq_executive_address1_cbsa_name"),
        ("Congressional District", "bq_executive_address1_congressional_district"),
        ("ZIP", "bq_executive_address1_zip5"),
        ("Direct Address", "bq_executive_address1_direct_indicator"),
        ("Address Valid", "bq_executive_address1_valid_indicator"),
    ]

    return [
        {"field": label, "value": str(_get_field(rec, key))} for label, key in fields
    ]


@register_widget(
    {
        "name": "Executive's Company",
        "description": "Company details for the selected executive's organization",
        "type": "table",
        "endpoint": "executive_company",
        "gridData": {"w": 20, "h": 15},
        "source": "BrightQuery",
        "params": EXEC_DETAIL_PARAM,
        "data": {
            "columnsDefs": [
                {
                    "field": "field",
                    "headerName": "Field",
                    "width": 200,
                    "cellDataType": "text",
                },
                {
                    "field": "value",
                    "headerName": "Value",
                    "width": 300,
                    "cellDataType": "text",
                },
            ]
        },
    }
)
@app.get("/executive_company")
async def executive_company(
    exec_id: str = Query("", description="Executive ID"),
    x_bq_username: str = Header(...),
    x_bq_password: str = Header(...),
):
    """Company details for the selected executive's organization."""
    auth = (x_bq_username, x_bq_password)
    rec = await _get_exec_detail_record(exec_id, auth)
    if not rec:
        return []

    rows = [
        ("Company Name", str(_get_field(rec, "bq_organization_name"))),
        ("Company Type", str(_get_field(rec, "bq_organization_company_type"))),
        ("Public/Private", str(_get_field(rec, "bq_organization_public_indicator"))),
        ("Active Status", str(_get_field(rec, "bq_organization_active_indicator"))),
        ("Industry", str(_get_field(rec, "bq_organization_naics_name"))),
        ("Sector", str(_get_field(rec, "bq_organization_naics_sector_name"))),
        ("IRS Industry", str(_get_field(rec, "bq_organization_irs_industry_name"))),
        ("Structure", str(_get_field(rec, "bq_organization_structure"))),
        ("Revenue", _format_currency(rec.get("bq_revenue_mr"))),
        ("Employees", _format_number(rec.get("bq_employment_mr"))),
        ("Address", str(_get_field(rec, "bq_organization_address1_line_1"))),
        ("City", str(_get_field(rec, "bq_organization_address1_city"))),
        ("State", str(_get_field(rec, "bq_organization_address1_state"))),
        ("Organization ID", str(_get_field(rec, "bq_organization_id"))),
    ]

    return [{"field": label, "value": value} for label, value in rows]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=7780)
