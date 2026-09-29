# ExpTracker by Wickz

A private, multi-user expense tracker I built end to end: a Python API, a SQL database and a dependency-free JavaScript frontend. Each person creates an account and works in their own isolated space. Because the data is financial, privacy and correctness drove most of the design decisions.

## Highlights
- **Per-user isolation:** every query is scoped to the signed-in user, so one account can never read or change another's records.
- **Period-based budgeting:** a summary of income, expenses, balance, days remaining and daily average for a period that defaults to the current calendar month.
- **Visual insight:** three pie charts and a month-on-month expense chart with a trend line and category filter, all drawn as plain SVG with no charting library.
- **Safe CSV import:** validated and all-or-nothing, with a downloadable template.
- **Exact money handling:** amounts stored as integer cents, never floats.
- **Installable app:** a Progressive Web App today, and packaged as a real Android app via a Trusted Web Activity (TWA) wrapper — same codebase, no rewrite.

## Tech stack
| Layer | Choice |
|---|---|
| Frontend | HTML, CSS and vanilla JavaScript (no build step), served by the backend |
| Backend | Python, FastAPI, SQLAlchemy 2 |
| Database | SQLite locally; any Postgres (for example a free-tier Neon database) through one `DATABASE_URL` setting |
| Auth | Email and password (Argon2id), session in an HttpOnly cookie signed with PyJWT |
| Charts | Hand-written SVG |
| Android packaging | [Bubblewrap](https://github.com/GoogleChromeLabs/bubblewrap) (Trusted Web Activity) over the same live site |

## Features
- Sign up and sign in with email and password; each account starts empty.
- Record transactions with a date, optional income type, optional expense type, description and amount. A row can carry both types.
- Type arithmetic straight into the amount field (for example `300+320`); it is parsed safely, never with `eval`.
- Manage your own income and expense categories.
- Overview: balance, per-category totals, days until the period ends and the daily average.
- Period defaults to the current month (1st to last day) and can be overridden with a custom Period Starting Date and Period Ending Date.
- Transaction list with Current period, All, or a From / To date range; rows outside the current period are dimmed.
- Import transactions from CSV; export a header-only template from the app.
- Visualisation tab:
  - Balance pie (income vs expenses)
  - Expenses by category pie
  - Income by category pie
  - Month-on-month expenses by calendar month, with a line across the column tops, a linear trend line and an expense-category filter
- Installable as a Progressive Web App (manifest + offline app-shell caching via a Service Worker), and wrapped as a native Android app with no address bar via Google's Trusted Web Activity approach

## How the numbers work
- **Current period:** `Period Starting Date <= date <= Period Ending Date`, both inclusive.
- **Category totals:** the sum of amounts per category over current-period rows. A row with both an income and an expense type counts on both sides.
- **Balance:** total income plus total expenses, where expenses are shown as negative.
- **Days remaining:** rounded days from now (in the user's timezone) to the end of the Period Ending Date.
- **Daily average:** balance divided by days remaining, rounded to two decimals.
- **Week #:** Week 1 starts at the period start and increases on each Monday whose date differs from the previous row.

## Privacy and security
- The user id always comes from the session, never from the request. Other users' records return "not found", and category ids from other accounts are rejected.
- Passwords hashed with Argon2id; login attempts throttled; generic login errors.
- Session cookie is `HttpOnly` and `SameSite=Strict` (and `Secure` over HTTPS) with a 12-hour lifetime.
- CSRF header check on all writes, a strict Content-Security-Policy, and `Cache-Control: no-store` on API responses.
- Same origin only (no CORS); API docs disabled.
- CSV import is size-limited (2 MB, 5,000 rows) and rejects the whole file if any row is invalid.

## Project structure
```
app/
  main.py                  API, auth, data model, calculations, CSV import, charts data,
                           and the routes serving manifest.json, sw.js and assetlinks.json
  assets/png/              site icon (favicon.svg)
  static/
    index.html             page shell and styles
    app.js                 user interface and SVG charts
    manifest.json          Web App Manifest (name, colors, icons, display mode)
    sw.js                  Service Worker (caches the static app shell only, never API data)
    icons/                 192x192, 512x512 and maskable 512x512 PNG icons for install/Android
    .well-known/
      assetlinks.json      Digital Asset Links - proves the domain owns the Android app
twa-manifest.json          Bubblewrap config for the Android (TWA) build
requirements.txt           Python dependencies
```

## Data model
- `users`: email, password hash, optional manual period start and end, timezone, currency.
- `cats`: per-user income and expense types.
- `txns`: per-user transactions with an optional income type and/or expense type; integer-cent amounts plus the original expression if one was typed.

## API overview (all under `/api`, JSON, same origin)
| Endpoint | Purpose |
|---|---|
| `POST /auth/signup`, `/auth/login`, `/auth/logout`, `GET /auth/me` | account and session |
| `GET /summary` | period dates, balance, days left, daily average, per-category totals |
| `PUT /settings` | manual period dates (empty resets to auto), timezone, currency |
| `GET /charts/monthly-expenses` | expense totals per calendar month, optional `category_id` |
| `POST /categories`, `DELETE /categories/{id}` | manage types (a type in use cannot be deleted) |
| `GET /transactions` | list, with `scope=current\|all` or `date_from` / `date_to` |
| `POST`, `PUT`, `DELETE /transactions[/{id}]` | add, edit, delete |
| `POST /import`, `GET /import/template` | CSV import and header-only template |

## Design notes
- **Integer cents** avoid floating-point drift in financial totals.
- **Server-side calculations:** the API returns finished summaries, so the frontend only renders.
- **No frontend dependencies:** plain JavaScript and SVG keep the app small and let it run under a strict Content-Security-Policy.
- **Automatic upgrades:** on start, the app migrates databases created by earlier versions.

## Known limits
- No email verification, password reset or field-level encryption at rest.
- Timezone and currency are stored per user but have no screen yet (defaults Asia/Colombo and LKR); the API accepts changes.
- Login throttling is in memory, so it suits a single instance.
- Free database tiers usually have no verified backups, so export or dump data regularly.

## Android app (Bubblewrap / Trusted Web Activity)
The web app is also shipped as an installable Android app, with no rewrite and no separate codebase to maintain. This uses Google's [Trusted Web Activity](https://developer.chrome.com/docs/android/trusted-web-activity/) approach: the Android app is a thin native shell that launches the live site full-screen, with no browser address bar, once the domain is verified as belonging to that app.

**How it fits together**
- `app/static/manifest.json` — the Web App Manifest: name, theme colors, display mode and icon set. This is what makes the site installable from a browser ("Add to Home screen") even before any Android packaging happens.
- `app/static/sw.js` — a minimal Service Worker. It only caches the static app shell (the HTML/JS/CSS and the manifest itself) so the app opens instantly and works offline for its shell; it deliberately never caches anything under `/api/`, so financial data is always fetched fresh and nothing sensitive is stored offline.
- `app/static/.well-known/assetlinks.json` — the Digital Asset Links file. Android fetches this live from the domain to confirm the app claiming to represent it was signed with the matching key; without it, the app falls back to showing a browser-style address bar.
- `twa-manifest.json` — [Bubblewrap's](https://github.com/GoogleChromeLabs/bubblewrap) configuration for generating the actual Android project: package id, host, icons, colors, signing key path.