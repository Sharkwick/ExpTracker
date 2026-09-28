# Monthly Expenses

A private, multi-user web app that reproduces the calculation logic of the `Monthly_Expenses_2026.xlsx` workbook. Each person signs up with an email and password and works in their own isolated environment. Setup steps are in `instructions.txt`.

## What it does
- Record transactions: date, income type, expense type, description, amount. A row can carry both an income and an expense type, as in the sheet.
- Summarise the current period: income and expenses per category, total balance, days remaining and daily average.
- View a **Charts** tab: pie charts for balance (income vs expenses), expenses by category and income by category (current period), and a month-on-month expense chart (calendar months) with a line over the column tops, a linear trend line and an expense-category filter. Charts are plain SVG, no external libraries.
- Look at any other dates with a From / To filter on the transaction list.
- Manage your own income and expense types.
- Import transactions from CSV (a template is downloadable in the app).

## Architecture
| Layer | Choice |
|---|---|
| Frontend | Single page, plain HTML + JavaScript (no build step), served by the backend |
| Backend | Python, FastAPI, SQLAlchemy |
| Database | SQLite locally; any free-tier Postgres (Neon, Supabase, Aiven) via `DATABASE_URL` |
| Auth | Email + password (Argon2id), session in an HttpOnly cookie |

## Project structure
```
README.md            this overview
instructions.txt     setup, run, deploy, troubleshooting
requirements.txt     Python dependencies
app/
  main.py            API, auth, data model, calculations, CSV import
  static/
    index.html       page shell and styles
    app.js           user interface logic
```

## Data model
- `users`: email, password hash, `start_override` and `end_override` (manual Period Starting / Ending Date; empty means auto), timezone, currency.
- `cats`: per-user income and expense types (unique per user, kind and name).
- `txns`: per-user transactions with an optional income type and/or expense type; amounts stored as exact integer cents, plus the original expression text if one was typed (for example `300+320`).

New accounts start empty: no default categories, transactions or period dates are created.

**Database upgrades.** On start, `main.py` upgrades databases created by earlier versions: it adds `start_override`, clears old end-date overrides (they followed the retired salary-date rule) and drops the retired `period_start` column. Dropping a column needs SQLite 3.35+ (any Postgres is fine); if it cannot be dropped a note is printed and the fix is to delete `expenses.db` and re-import from CSV. `end_override` is stored in the column named `next_override` so older databases need no rename. New databases need none of this.

## API (all under `/api`, JSON, same origin)
| Endpoint | Purpose |
|---|---|
| `POST /auth/signup`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | account and session |
| `GET /summary` | period dates, balance, days left, daily average, per-category totals, category list |
| `PUT /settings` | manual Period Starting / Ending Date (empty resets to auto), timezone, currency |
| `GET /charts/monthly-expenses` | expense totals per calendar month, optional `category_id`; months without spending appear as 0 (latest 60) |
| `POST /categories`, `DELETE /categories/{id}` | manage types (a type used by transactions cannot be deleted) |
| `GET /transactions` | list; `scope=current\|all`, or `date_from` / `date_to` (inclusive) for a custom range |
| `POST /transactions`, `PUT /transactions/{id}`, `DELETE /transactions/{id}` | add, edit, delete |
| `POST /import`, `GET /import/template` | CSV import (all-or-nothing) and header-only template |

## Calculation logic (mapped from the workbook)
| Workbook | App |
|---|---|
| Month Starting / next date | **Period Starting Date** and **Period Ending Date**. Auto (default): current month in the user's timezone, 1st to last day; if only the start is overridden, the auto end is the last day of that month. Either can be overridden, and a start after the end is rejected. |
| `Transactions!F` Current Month? | `Period Starting Date <= date <= Period Ending Date` (both inclusive) |
| `Transactions!G` Week # | Week 1 at period start; +1 on a Monday whose date differs from the previous row |
| Summary income / expense rows | `SUMIFS` per category over current-period rows; a row with both types counts on both sides |
| Expenses total | shown negative; **Balance = income + (-expenses)** |
| Current date / days left / daily average | now in the user's timezone (default Asia/Colombo, +330 min); days = ROUND((Period Ending Date + 1 day) - now); daily average = ROUND(balance / days, 2) |
| `=400+300` in Amount | typing `400+300` works; parsed safely, never with `eval` |

## Privacy and security
- Every query is filtered by the signed-in user's id taken from the session, never from the request. Other users' records return "not found", and category ids from other accounts are rejected.
- Passwords hashed with Argon2id; login attempts throttled; generic login errors.
- Session cookie is `HttpOnly` and `SameSite=Strict` (and `Secure` over HTTPS), 12-hour lifetime.
- CSRF header check on all writes, strict Content-Security-Policy, `Cache-Control: no-store` on API responses.
- Same-origin only (no CORS); API docs disabled.
- Import is all-or-nothing and size-limited (2 MB, 5,000 rows).

## Known limits
- No email verification, password reset, or field-level encryption at rest.
- Timezone and currency are stored per user but have no screen yet (defaults Asia/Colombo and LKR); the API accepts changes.
- Login throttling is in memory, so run a single instance.
- Free database tiers usually have no verified backups: export or dump your data regularly.