# TASMAC Region-Based AI Grievance Analysis and Management

English, text-only grievances. Region is an access-control boundary, not a hierarchy.
A grievance belongs to a region and optionally an outlet (region is derived from the outlet when one is chosen).

## Run locally (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r backend\requirements.txt

cd backend
$env:SEED_PASSWORD = "choose-a-password"      # omit to generate and print one
..\.venv\Scripts\python -m app.seed --demo-data   # Tamil Nadu regions/outlets, accounts, ~300 historical grievances
..\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

```powershell
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to port 8000
```

Two logins are enabled (password = the one printed or set above): `admin@tasmac.local` (global admin, all regions) and
`admin.chennai@tasmac.local` (Chennai region admin). The other 18 seeded accounts (management, other regions, officers) still exist but are
disabled, so history keeps its assignee names. To enable one, add its email to `ACTIVE_LOGINS` in `backend/app/seed.py` and run `python -m app.seed`.
With no active officers, new complaints are assigned to the region admin, and the global admin can assign any region's complaint to themselves.
The seed has 6 Tamil Nadu regions with 29 outlets named after real localities (Anna Nagar, Gandhipuram, Simmakkal, ...). The grouping and outlet codes are
**placeholders, not TASMAC's official zones or shop numbers**. Replace them via `POST /api/admin/regions` (with `places`) and `/api/admin/outlets` (with `area`).
`--demo-data` writes about 300 grievances over 90 days straight to the database (no AI calls, labelled `seed-data`) with hot spots,
recurring issues, duplicates, SLA misses and escalations. It only runs on an empty grievance table. After changing the models, delete `tasmac.db` and seed again.

Tests: `cd backend; ..\.venv\Scripts\python -m pytest`

## Citizen intake: text chat bot

The home page is a chat bot (`POST /api/public/chat`, code in `services/chatbot.py`). It asks questions until the form is complete:
what happened (it probes up to twice when the description is vague), region, outlet (or "none" for a general issue), and an optional email.
It then shows a summary and submits **only after the citizen types an explicit yes** ("yes", "yes, submit", "confirm", ...).

- Gemini reads each message and fills the draft fields. It never decides to submit. Confirmation is matched against fixed phrases in code,
  so a model reply or text like "submit it now" cannot trigger a submission.
- The complaint is stored as the citizen's own words, joined together, not rewritten by the model.
- The citizen types where it happened (for example "Anna Nagar, Chennai" or "Tiruppur"). Gemini maps the place to a region and outlet using each region's
  `places` list and each outlet's `area`. If a locality exists in several regions (Anna Nagar is in Chennai and Madurai) or nothing matches, the bot asks again.
- A double "yes" creates one grievance (compare-and-swap on the session). The tracking code is shown once and is never stored in the chat transcript.
- If the Gemini call fails, a keyword extractor keeps the conversation going. Sessions expire after 24 hours.
- `POST /api/public/grievances` still exists as a plain API for integrations and tests. It has no confirmation step, so don't expose it in a public UI.
## Find stock near me (public)

`/stores` shows the nearest shops and what each has in stock. No login.

- The browser asks for the citizen's location (`navigator.geolocation`; works on `localhost` and HTTPS only). It is sent in a POST body to
  `POST /api/public/stores/nearby`, so it never appears in URLs or access logs, and it is not stored. If permission is refused, the citizen picks a district.
- Results are the nearest shops within the chosen radius (5/10/25/50 km), nearest first, with straight-line distance, the in-stock products with MRP,
  a "few left" flag at 5 units or fewer, a Directions link and a map (OpenStreetMap tiles via Leaflet). Out-of-stock products are hidden, and stock
  older than 24 hours is marked "may be out of date". A product box and category chips filter to shops that have the item.
- Shops live in their own `stores` table, separate from the grievance `outlets`, so loading thousands of shops does not touch the complaint flow.
  Search ranks shops by distance first and loads stock only for the few it returns, so it copes with a full state list.

### Loading the official shop list

```powershell
cd backend
..\.venv\Scripts\python -m app.import_stores shops.csv --dry-run   # check the file, write nothing
..\.venv\Scripts\python -m app.import_stores shops.csv             # import
```

Needed columns: `shop_code`, `district`, `lat`, `lng`. Optional: `name`, `address`, `area`. Common header variants are accepted (`Shop No`, `Latitude`, `District Name`, ...).
Rows without usable coordinates, outside Tamil Nadu, or with duplicate codes are skipped and listed. Re-running the same file updates shops, it never duplicates.
Once any real shop exists the public search hides the sample shops (they stay in the database). **The repo does not contain an official list.** TASMAC's website
could not be read automatically, so get the list from TASMAC or the state government. If it only has addresses, the coordinates must be added first (a geocoder
such as the Google Geocoding API, with its terms and cost, or TASMAC's own coordinates).

### Real shops from OpenStreetMap (loaded)

No official TASMAC list was available (tasmac.tn.gov.in cannot be read automatically), so shops come from OpenStreetMap, which is open data under the ODbL
(credit "OpenStreetMap contributors"; a public database built from it must stay under the same licence).

```powershell
cd backend
..\.venv\Scripts\python -m app.fetch_osm_stores      # one Overpass API request -> data/osm_stores.csv (TASMAC-tagged shops only)
..\.venv\Scripts\python -m app.fill_districts        # reverse-geocodes each shop's district with Nominatim, 1 request/second
..\.venv\Scripts\python -m app.import_stores data\osm_stores.csv
```

**Coverage is very small.** OpenStreetMap lists only 44 TASMAC-tagged shops for all of Tamil Nadu (the state has about 4,800). Volunteers have mapped some districts
and not others, so many places have no shop within 10 km. Names are mostly just "TASMAC", and addresses are mostly empty. This is a stop-gap, not a substitute for the
official list. Loading the official list later (same importer) updates by shop code and the page then shows only the real shops.
### What is real and what is not

- Shops, after an import: real, as accurate as the file you load.
- Products, prices and stock: **synthetic.** A generic catalogue with made-up prices and random quantities, generated for every shop that has none.
  The page can therefore send people to a real shop for an item that is not there. Keep the demo notice on (`STORE_DATA_IS_SAMPLE=true`) until stock comes from a real feed.
- Staff replace synthetic stock with `PUT /api/stock` (admin only; one shop and product at a time), add products with `POST /api/admin/products`, and add a single shop
  with `POST /api/admin/stores`. There is no stock screen in the UI yet.
## Roles

| Role | Scope | Can |
|---|---|---|
| admin | all regions | everything, manage regions, outlets, users, categories, SLA |
| management | all regions | read dashboards and grievances only |
| region_admin | own region | review AI-flagged grievances, assign, resolve, reopen, reanalyze |
| officer | own region | work grievances assigned to them |

Region isolation is enforced in the API layer (`deps.resolve_region_scope`, `deps.get_scoped_grievance`) and in
similarity search, which only ever looks inside the grievance's own region. Out-of-region records return 404.

## AI pipeline

One LangGraph workflow (`backend/app/ai/workflow.py`):
`normalize -> analyze -> embed -> retrieve -> verify (rerank) -> classify -> validate -> flag_review | route_queue`

- Analysis: category, related categories, sentiment, severity, summary, alleged issues, entities, recommended action, confidence.
  Stored in `grievance_analysis` with model and prompt version. Every reanalysis or human override adds a row; the latest is current.
- Relationships (`grievance_relationships`): `duplicate` (same outlet, within 14 days, near-identical, same contact or similarity >= 0.97) and `related` (same recurring problem from different people).
  Related complaints in the same region within 90 days form a **recurring issue** once there are 3 or more.
- Needs human review when: confidence below 0.6, category `Other`, a duplicate is suspected, or a provider call failed.
  Those go to `PENDING_REVIEW` for the region admin. Everything else is auto-assigned to the least-loaded officer in the region.
- The original description is immutable (enforced in the ORM). Issues are labelled as allegations.

### Providers

One Gemini API key (`GOOGLE_API_KEY`) covers everything. The app reads `.env` from the project root or `backend/`:
`LLM_MODEL` (default `gemini-2.5-flash`) does the analysis and `EMBEDDING_MODEL` (default `models/gemini-embedding-001`) does the embeddings.
Embeddings are requested at `EMBEDDING_DIM` (default 768) and normalised.

Gemini has no rerank endpoint, so similarity verification uses cosine thresholds plus a category check: two complaints only count as related
if their categories match or overlap. Starting thresholds are `RELATED_THRESHOLD=0.84` and `DUPLICATE_THRESHOLD=0.95`. They come from a
handful of samples, so calibrate them on real complaints. Embeddings from different models are never compared with each other.

Without a key the app runs on local heuristic providers (keyword rules and a hashing embedder), labelled `heuristic-*` on every analysis.
If a Gemini call fails or returns invalid JSON twice, the grievance gets a low-confidence heuristic analysis and goes to human review.

## Database

SQLite (`DATABASE_URL=sqlite:///./tasmac.db`, created relative to where you start the server, so run from `backend/`). Vector search runs in Python
over the grievance's own region and the last 180 days, which is fine at this scale. Do not point `DATABASE_URL` at another application's database:
the app creates its own tables at startup.
## SLA, escalation, reopening

Targets by severity (editable via `PUT /api/admin/sla`): Critical 24h, High 72h, Medium 7d, Low 14d. A background loop flags overdue grievances as escalated every 10 minutes
(`POST /api/admin/escalations/run` triggers it). Citizens can reopen a resolved grievance within 14 days using the tracking code, and staff can reopen at any time.

## Not included yet

Alembic migrations (schema is created with `create_all`), SQS/Redis workers (processing runs as a FastAPI background task),
a separate reasoning model for deep analysis, predictive analytics, AWS ECS and CloudWatch deployment config, password reset and user management UI.
