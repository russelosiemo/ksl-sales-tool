# KSL Field Sales Tool

Mobile-first progressive web app for sales rep customer visits.
Hosted on GitHub Pages. No server. No infrastructure. No cost.

## Repository layout

```
ksl-sales-tool/
  pipeline/               Python pipeline (GitHub Actions only)
    zoho_client.py        Single Zoho API abstraction layer
    token_refresh.py      Writes fresh access token hourly
    build_catalogue.py    Builds catalogue.json weekly
    build_velocity.py     Builds velocity.json + ABC classes weekly
    stock_watcher.py      Smart delta stock refresh hourly
    build_reps.py         Converts CSV to reps.json
  app/
    index.html            Complete React PWA (single file)
    services/             JS service modules (reference only)
    state/                JS state modules (reference only)
  data/
    app/                  Generated JSON files (gh-pages branch only)
    config/
      reps_customers.csv  Rep to customer mapping (manually maintained)
  tests/                  Python unit tests, one file per pipeline module
  .github/workflows/
    data_refresh.yml      Two jobs: hourly stock watch, nightly full build
```

## Setup

### 1. Create the repository

Create a new private GitHub repository named `ksl-sales-tool`.

### 2. Create two branches

- `main` — source code, pipeline scripts, tests
- `gh-pages` — built app files and data, served by GitHub Pages

Enable GitHub Pages in repo Settings, set source to `gh-pages` branch, root folder.

### 3. Add GitHub Secrets

Go to Settings > Secrets and variables > Actions > New repository secret:

| Secret               | Value                                                                     |
| -------------------- | ------------------------------------------------------------------------- |
| `ZOHO_CLIENT_ID`     | Your Zoho OAuth client ID                                                 |
| `ZOHO_CLIENT_SECRET` | Your Zoho OAuth client secret                                             |
| `ZOHO_REFRESH_TOKEN` | Your Zoho refresh token                                                   |
| `ZOHO_ORG_ID`        | Your Zoho organisation ID (800177077)                                     |
| `ZOHO_MAIN_WH1`      | Exact name of main warehouse 1 in Zoho                                    |
| `ZOHO_MAIN_WH2`      | Exact name of main warehouse 2 in Zoho                                    |
| `DEPLOY_TOKEN`       | GitHub Personal Access Token with repo scope (needed to push to gh-pages) |

### 4. Add the rep-customer CSV

Edit `data/config/reps_customers.csv`. Format:

```
rep_id,rep_name,pin,zoho_customer_id,customer_name
REP001,Jane Mwangi,123456,123456789,Naivas Westgate
REP001,Jane Mwangi,123456,123456790,Quick Mart Lavington
REP002,Brian Otieno,5678,123456791,Naivas Junction
```

The `zoho_customer_id` is the Zoho contact ID, visible in the Zoho Inventory
contact URL. The `pin` is the raw 4-digit PIN — it is hashed before being
written to the output file. The raw PIN never leaves the repo CSV.

### 5. Run the first nightly build manually

Go to Actions > KSL Sales Tool Data Refresh > Run workflow > select `nightly-build`.

This builds all four data files and deploys them to `gh-pages`.

### 6. Copy the app to gh-pages

Copy `app/index.html` to the root of the `gh-pages` branch.
Create `data/app/` directory on that branch (the workflow populates it).

The app URL will be `https://your-github-username.github.io/ksl-sales-tool/`.

## Local development

```bash
python -m venv venv
source venv/bin/activate   # or venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env
# fill in .env with real credentials
python -m pipeline.build_catalogue
python -m pipeline.build_velocity
python -m pipeline.stock_watcher --full
python -m pipeline.build_reps
python -m pipeline.token_refresh
```

Then open `app/index.html` in a browser via a local server (not file://):

```bash
cd app && python -m http.server 8080
```

Visit `http://localhost:8080`.

## Running tests

```bash
python -m pytest tests/ -v
```

Tests are mocked — no network calls, no credentials required.

## Updating rep assignments

Edit `data/config/reps_customers.csv` and push to `main`.
The nightly build job will regenerate `reps.json` automatically.
To apply immediately, trigger `nightly-build` manually from GitHub Actions.

## ABC classification

SKUs are classified weekly based on cumulative revenue contribution:

- Class A: top 20% of total revenue (fast movers)
- Class B: next 30% of total revenue
- Class C: remaining 50%

Buffer weeks applied to recommended order calculation:

- A: 1.0 week buffer
- B: 0.75 week buffer
- C: 0.5 week buffer
