# BrightQuery KYB

Know Your Business data powered by BrightQuery's Append API, built as an OpenBB Workspace app.

Provides company firmographic, financial, risk/compliance, funding, corporate structure, and executive data for 84M+ US organizations.

## Dashboards

- **Company Lookup** — Search companies by name, ticker, website, EIN, CIK, or address. Click a row to populate detail widgets: Company Profile, Risk & Compliance, Financials & Growth, Funding, and Corporate Structure.
- **People Lookup** — Search executives by name, LinkedIn URL, or email. Click a row to view Executive Profile and their Company details.

## Prerequisites

- Python 3.10+
- A BrightQuery account with API credentials (username and password)

## Getting Started

1. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

2. **Run the server**

   ```bash
   uvicorn main:app --host 0.0.0.0 --port 7780
   ```

   Or directly:

   ```bash
   python main.py
   ```

3. **Connect to OpenBB Workspace**

   Add `http://localhost:7780` as a custom backend in OpenBB Workspace.

   Required Headers:
   - X-BQ-USERNAME
   - X-BQ-PASSwORD

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Health check |
| `GET /widgets.json` | Widget definitions |
| `GET /apps.json` | Dashboard layouts |
| `GET /company_search` | Search companies |
| `GET /company_profile` | Company firmographic details |
| `GET /risk_compliance` | Risk & compliance indicators |
| `GET /financials_growth` | Revenue, margins, growth metrics |
| `GET /funding` | Funding rounds and amounts |
| `GET /corporate_structure` | Legal entity hierarchy |
| `GET /executive_search` | Search executives |
| `GET /executive_profile` | Executive contact & profile |
| `GET /executive_company` | Executive's company details |
