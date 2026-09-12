# Flock Energy Assignment Reference Document

## 1. Objective

The goal of this project is to build a clean, modern, and well-documented **REST API wrapper** around an existing legacy web application ("Urja Meter Ops").

The legacy portal currently requires manual browser navigation to view smart meter data, hierarchy, and consumption stats. Your API service will automate authentication and session management against the portal, fetch data programmatically, transform messy or HTML-heavy responses into clean structured JSON, and expose documented REST endpoints for downstream clients.

## 2. Technical Requirements

### Required Software & Tools

- **Node.js (v18+)** or **Python (3.10+)** — Language runtime environment.
- **HTTP Client Library**:
    - *Python*: `requests` or `httpx` (supports session persistence and cookies).
    - *Node.js*: `axios` with `axios-cookiejar-support` or `fetch`.
- **Web Framework**:
    - *Python*: `FastAPI` (automatically generates OpenAPI specifications) or `Flask`.
    - *Node.js*: `Express.js` or `Fastify`.
- **HTML Parser (if endpoints return HTML tables/pages)**:
    - *Python*: `BeautifulSoup4` or `selectolax`.
    - *Node.js*: `cheerio`.
- **Developer Tools**: Web Browser (Chrome/Firefox DevTools) for inspecting HTTP network requests.
- **Git**: For version control.

### Hardware Specifications

- Any modern computer (Windows, macOS, or Linux) with at least 4 GB RAM and internet access.

## 3. Step-by-Step Instructions

### Step 1: Reconnaissance & Network Inspection

Before writing code, inspect how the legacy portal behaves under the hood.

1. Open Chrome/Firefox DevTools (`F12` or `Ctrl+Shift+I` / `Cmd+Option+I`) and switch to the **Network** tab.
2. Check **Preserve log** to keep HTTP requests across redirects.
3. Log in to `https://urja-ops.flockenergy.tech` using the credentials provided (`operator@urja.local` / `urja-ops-2026`).
4. Note the login request:
    - Is it a `POST` request?
    - What form fields or JSON payload are sent (`username`, `password`, CSRF tokens)?
    - Look at the response headers: Are session cookies (`Set-Cookie`) set?

5. Navigate through meter listings, detail pages, and consumption pages. Observe:
    - Are there hidden internal JSON APIs (e.g., `/api/v1/meters`) being called by frontend scripts?
    - If responses are pure HTML, identify HTML elements (`<table>`, `<div>` IDs, classes) containing the target data.


### Step 2: Define your Protocol Discovery (`PROTOCOL.md`)

Document your findings in `PROTOCOL.md` as you inspect the portal. Record:

- Authentication workflow (Session cookies, bearer tokens, CSRF tokens).
- Internal endpoints discovered (URLs, HTTP methods, parameters).
- Data structures and anomalies (e.g., missing fields, inconsistent dates, nested tables).


### tep 3: Set Up the Project & Environment

1. Initialize a new Git repository:
    
    ```
    git init flock-energy-api
    cd flock-energy-api
    ```
    
2. Set up your language runtime and dependencies (e.g., `pip install fastapi uvicorn httpx beautifulsoup4` or `npm init -y && npm install express axios cheerio`).


### Step 4: Build the Legacy Client / Adapter Layer

Create an adapter module responsible purely for talking to the Urja portal:

1. **Session Persistence**: Maintain a persistent session instance that stores cookies after login.
2. **Auto-Reauthentication**: Detect when a session has expired (e.g., 401 response or redirect to login) and automatically log in again.
3. **HTML Parsing / Data Normalization**: If the portal returns HTML, parse tables into structured dictionary/JSON formats. Clean up whitespace and normalize data types (e.g., convert numeric string `"124.50"` to float `124.50`).


### Step 5: Build the REST API Layer

Expose clean, intuitive REST endpoints using your framework of choice:

- `POST /api/v1/auth/login` (or manage session automatically behind the scenes).
- `GET /api/v1/meters` — Retrieve a list of smart meters.
- `GET /api/v1/meters/{id}` — Get specific meter details.
- `GET /api/v1/meters/{id}/consumption` — Get consumption history.
- `GET /api/v1/hierarchy` — (Optional Extension) Retrieve organized network tree.

### Step 6: Generate OpenAPI Documentation (`openapi.json`)

- If using **FastAPI**, export the automatically generated schema at `/openapi.json`.
- If using **Express.js**, write the specification manually using YAML/JSON or use libraries like `swagger-jsdoc`.
- Serve a Swagger UI or Scalar documentation page at `/docs` for easy interactive testing.


### Step 7: Write Documentation and Reflection

1. **README.md**: Include setup commands, sample request/response curls, list of architectural trade-offs, and intentional omissions.
2. **REFLECTION.md**: Answer the required reflection questions thoroughly (e.g., assumptions made, difficult parts, what mistakes occurred, future improvements).


## 4. Project Structure

Here is a recommended project layout for a Python/FastAPI implementation (Node.js/Express follows a similar modular pattern):

flock-energy-api/
│
├── app/
│   ├── __init__.py
│   ├── main.py              # Application entry point & API route definitions
│   ├── client.py            # Legacy Urja Portal HTTP adapter & scraper logic
│   ├── models.py            # Data models / validation schemas (Pydantic / Interfaces)
│   └── config.py            # Environment variables & default settings
│
├── openapi.json             # Exported OpenAPI 3.0 specification
├── PROTOCOL.md              # Detailed documentation of legacy portal behavior
├── REFLECTION.md            # Answers to reflection questions (or inside README.md)
├── README.md                # Main documentation, setup instructions, & architectural decisions
├── requirements.txt         # Project dependencies (or package.json)
└── .gitignore               # Files to ignore in version control


## 5. Key Example Code Snippets

### A. Python (`httpx` + `FastAPI`) Scraper & Client Example

# app/client.py
import httpx
from bs4 import BeautifulSoup

class UrjaPortalClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        # Persistent client to hold session cookies
        self.client = httpx.Client(timeout=10.0)

    def login(self, username: str, password: str) -> bool:
        login_url = f"{self.base_url}/login"
        payload = {"username": username, "password": password}
        response = self.client.post(login_url, data=payload)

        # Check if session cookie was established or redirect succeeded
        return response.status_code in (200, 302)

    def get_meter_details(self, meter_id: str) -> dict:
        url = f"{self.base_url}/meters/{meter_id}"
        response = self.client.get(url)

        if response.status_code == 401:
            raise Exception("Session expired or unauthorized")

        # HTML parsing example if legacy portal returns raw HTML
        soup = BeautifulSoup(response.text, "html.parser")

        serial_elem = soup.find("span", {"id": "meter-serial"})
        status_elem = soup.find("td", {"class": "status-value"})

        return {
            "meter_id": meter_id,
            "serial_number": serial_elem.text.strip() if serial_elem else "N/A",
            "status": status_elem.text.strip() if status_elem else "UNKNOWN"
        }


B. Python API Route Example

# app/main.py
from fastapi import FastAPI, HTTPException, Depends
from app.client import UrjaPortalClient
from pydantic import BaseModel

app = FastAPI(
    title="Flock Energy - Urja Meter Ops API",
    version="1.0.0",
    description="Clean REST API proxy layer over legacy Urja portal"
)

# Shared instance (in production, use dependency injection or session pool)
portal_client = UrjaPortalClient("https://urja-ops.flockenergy.tech")

class MeterResponse(BaseModel):
    meter_id: str
    serial_number: str
    status: str

@app.get("/api/v1/meters/{meter_id}", response_model=MeterResponse)
def read_meter(meter_id: str):
    try:
        data = portal_client.get_meter_details(meter_id)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


C. Node.js (axios + cheerio + express) Example

// src/client.js
const axios = require('axios');
const cheerio = require('cheerio');
const { wrapper } = require('axios-cookiejar-support');
const { CookieJar } = require('tough-cookie');

const jar = new CookieJar();
const client = wrapper(axios.create({ jar, withCredentials: true }));

async function getMeterDetails(meterId) {
  const url = `https://urja-ops.flockenergy.tech/meters/${meterId}`;
  const response = await client.get(url);

  const $ = cheerio.load(response.data);

  return {
    meter_id: meterId,
    serial_number: $('#meter-serial').text().trim(),
    status: $('.status-value').text().trim()
  };
}

module.exports = { getMeterDetails };



## Summary Checklist Before Submission

- [ ]  Verified all core dependencies install easily without missing packages.
- [ ]  Tested endpoint functionality against the live portal URL.
- [ ]  Exported/updated `openapi.json` and ensured it matches endpoint implementations.
- [ ]  Detailed all findings in `PROTOCOL.md`.
- [ ]  Completed `README.md` with setup instructions, sample requests, and trade-off notes.
- [ ]  Filled out `REFLECTION.md` addressing all 5 r