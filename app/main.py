# ---------- dependencies ----------
import ast, calendar, csv, io, os, re, secrets, time
from typing import Optional
from datetime import date as Date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, Date as SDate, ForeignKey, String, UniqueConstraint, create_engine, inspect, select, text as sql_text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

# ---------- config / db ----------
SECRET = os.getenv("SECRET_KEY") or secrets.token_hex(32)  # set SECRET_KEY in production or sessions reset on restart
SECURE = os.getenv("COOKIE_SECURE", "1") != "0"
DB = os.getenv("DATABASE_URL") or "sqlite:///" + (Path(__file__).resolve().parent.parent / "expenses.db").as_posix()
DB = re.sub(r"^postgres(ql)?://", "postgresql+psycopg2://", DB)
engine = create_engine(DB, pool_pre_ping=True)
Sess = sessionmaker(engine, expire_on_commit=False)
ph = PasswordHasher()  # argon2id
DUMMY = ph.hash("dummy-password")


class Base(DeclarativeBase): pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    pw: Mapped[str] = mapped_column(String(255))
    end_override: Mapped[Optional[Date]] = mapped_column("next_override", SDate, nullable=True)  # manual Period Ending Date; NULL = auto (DB column name kept for old databases)
    start_override: Mapped[Optional[Date]] = mapped_column(SDate, nullable=True)  # manual Period Starting Date; NULL = auto
    tz: Mapped[str] = mapped_column(String(64), default="Asia/Colombo")       # sheet: NOW()+330/1440
    currency: Mapped[str] = mapped_column(String(6), default="LKR")


class Cat(Base):
    __tablename__ = "cats"
    __table_args__ = (UniqueConstraint("user_id", "kind", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(7))  # income | expense
    name: Mapped[str] = mapped_column(String(60))


class Txn(Base):
    __tablename__ = "txns"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[Date] = mapped_column(SDate, index=True)
    income_cat_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cats.id"), nullable=True)
    expense_cat_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cats.id"), nullable=True)
    description: Mapped[str] = mapped_column(String(200), default="")
    amount: Mapped[int] = mapped_column(BigInteger)  # minor units (cents): exact, no float error
    expr: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # e.g. "300+320", like the sheet's =300+320


Base.metadata.create_all(engine)


def upgrade_legacy_db():
    """One-time upgrade for databases created before auto periods; does nothing on new databases."""
    cols = {c["name"] for c in inspect(engine).get_columns("users")}
    with engine.begin() as cn:
        if "start_override" not in cols:
            cn.execute(sql_text("ALTER TABLE users ADD COLUMN start_override DATE"))
            cn.execute(sql_text("UPDATE users SET next_override = NULL"))  # old end dates followed the retired exclusive rule
    if "period_start" in cols:  # retired column (old Month Starting Date)
        try:
            with engine.begin() as cn: cn.execute(sql_text("ALTER TABLE users DROP COLUMN period_start"))
        except Exception: print("Note: could not drop legacy column users.period_start (needs SQLite 3.35+). Delete expenses.db to start fresh.")


upgrade_legacy_db()


def get_db():
    with Sess() as s:
        yield s


# ---------- helpers ----------
OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}


def to_cents(s) -> int:
    """Accepts 1234.5 or arithmetic like '=400+300' (as typed in the sheet). No eval()."""
    s = str(s).strip().lstrip("=")
    if not s or len(s) > 100: raise ValueError

    def ev(n):
        if isinstance(n, ast.Constant) and type(n.value) in (int, float): return Decimal(str(n.value))
        if isinstance(n, ast.BinOp) and type(n.op) in OPS: return OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return -ev(n.operand) if isinstance(n.op, ast.USub) else ev(n.operand)
        raise ValueError
    try: v = ev(ast.parse(s, mode="eval").body)
    except Exception: raise ValueError
    c = int((v * 100).quantize(Decimal(1), ROUND_HALF_UP))
    if abs(c) > 10**13: raise ValueError
    return c


def money(c: int) -> str: return f"{Decimal(c) / 100:.2f}"


def xl_round(x: float, nd=0) -> Decimal:  # Excel ROUND: half away from zero
    return Decimal(str(x)).quantize(Decimal(1).scaleb(-nd), ROUND_HALF_UP)


def month_end(d: Date) -> Date: return Date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def resolve_period(u: User, today: Date):
    """Auto (default): current month, 1st to last day. Start and/or end may be overridden by the user."""
    auto_start = today.replace(day=1)
    start = u.start_override or auto_start
    auto_end = month_end(start)
    return start, auto_start, u.end_override or auto_end, auto_end


def compute(u: User, db: Session):
    cats = db.scalars(select(Cat).where(Cat.user_id == u.id).order_by(Cat.id)).all()
    txns = db.scalars(select(Txn).where(Txn.user_id == u.id).order_by(Txn.date, Txn.id)).all()
    now = datetime.now(ZoneInfo(u.tz)).replace(tzinfo=None)  # sheet: NOW()+330/1440
    start, auto_start, end, auto_end = resolve_period(u, now.date())
    inp = lambda t: start <= t.date <= end  # sheet: Transactions!F "Current Month?" (both dates inclusive)
    week, wk, prev = {}, 0, None                    # sheet: Transactions!G "Week #"
    for t in txns:
        if inp(t):
            wk = 1 if wk == 0 else wk + (1 if t.date.weekday() == 0 and t.date != prev else 0)
            week[t.id] = wk
        prev = t.date
    tot = lambda cid, f: sum(t.amount for t in txns if inp(t) and getattr(t, f) == cid)  # SUMIFS
    inc = [{"id": c.id, "name": c.name, "total": money(tot(c.id, "income_cat_id"))} for c in cats if c.kind == "income"]
    exp = [{"id": c.id, "name": c.name, "total": money(tot(c.id, "expense_cat_id"))} for c in cats if c.kind == "expense"]
    ti = sum(int(Decimal(r["total"]) * 100) for r in inc)
    te = -sum(int(Decimal(r["total"]) * 100) for r in exp)   # sheet: D20 = -SUM(...)
    bal = ti + te
    days = int(xl_round((datetime.combine(end + timedelta(days=1), datetime.min.time()) - now).total_seconds() / 86400))  # end date is inclusive
    days = "" if days < 0 else days
    daily = money(int(xl_round(bal / days, 0))) if days else None  # sheet: ROUND(D4/D8,2)
    return cats, txns, week, inp, {
        "title": f"Expenses Summary - {end.strftime('%b %Y')}", "period_start": str(start), "auto_period_start": str(auto_start), "start_is_override": bool(u.start_override),
        "period_end_date": str(end), "auto_period_end_date": str(auto_end), "end_is_override": bool(u.end_override), "current_date": now.isoformat(timespec="minutes"),
        "days_until_period_end": days, "daily_average": daily, "balance": money(bal),
        "income": inc, "income_total": money(ti), "expenses": exp, "expense_total": money(te),
        "currency": u.currency, "tz": u.tz}


# ---------- app / security ----------
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
hits: dict[str, list[float]] = {}


def throttle(key: str, n=8, win=600):
    now = time.time(); h = [t for t in hits.get(key, []) if now - t < win]
    if len(h) >= n: raise HTTPException(429, "Too many attempts. Try again later.")
    hits[key] = h + [now]


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "fetch":
        return JSONResponse({"detail": "Forbidden"}, 403)  # CSRF: custom header can't be sent cross-site without CORS
    r = await call_next(request)
    r.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                      "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"})
    if request.url.path.startswith("/api"): r.headers["Cache-Control"] = "no-store"
    return r


def me(request: Request, db: Session = Depends(get_db)) -> User:
    try: uid = jwt.decode(request.cookies.get("s", ""), SECRET, algorithms=["HS256"])["uid"]
    except Exception: raise HTTPException(401, "Not signed in")
    u = db.get(User, uid)
    if not u: raise HTTPException(401, "Not signed in")
    return u


def login_cookie(resp: JSONResponse, uid: int, request: Request):
    tok = jwt.encode({"uid": uid, "exp": datetime.now(timezone.utc) + timedelta(hours=12)}, SECRET, "HS256")
    resp.set_cookie("s", tok, max_age=43200, httponly=True, secure=SECURE and (request.url.scheme == 'https' or request.headers.get('x-forwarded-proto') == 'https'), samesite="strict", path="/")
    return resp


class Cred(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


@app.post("/api/auth/signup")
def signup(c: Cred, request: Request, db: Session = Depends(get_db)):
    email = c.email.strip().lower()
    throttle("su:" + request.client.host, 10)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): raise HTTPException(400, "Invalid email")
    if len(c.password) < 10: raise HTTPException(400, "Password must be at least 10 characters")
    if db.scalar(select(User).where(User.email == email)): raise HTTPException(409, "Email already registered")
    u = User(email=email, pw=ph.hash(c.password))
    db.add(u); db.flush()
    db.commit()
    return login_cookie(JSONResponse({"email": u.email}), u.id, request)


@app.post("/api/auth/login")
def login(c: Cred, request: Request, db: Session = Depends(get_db)):
    email = c.email.strip().lower()
    throttle(f"li:{request.client.host}:{email}")
    u = db.scalar(select(User).where(User.email == email))
    try: ph.verify(u.pw if u else DUMMY, c.password); ok = bool(u)
    except VerifyMismatchError: ok = False
    if not ok: raise HTTPException(401, "Invalid email or password")
    return login_cookie(JSONResponse({"email": u.email}), u.id, request)


@app.post("/api/auth/logout")
def logout():
    r = JSONResponse({"ok": True}); r.delete_cookie("s", path="/"); return r


@app.get("/api/auth/me")
def whoami(u: User = Depends(me)): return {"email": u.email, "tz": u.tz, "currency": u.currency}


# ---------- settings / categories ----------
class SettingsIn(BaseModel):
    period_start_override: Optional[Date] = None
    period_end_override: Optional[Date] = None
    tz: str = "Asia/Colombo"
    currency: str = Field("LKR", max_length=6)


@app.put("/api/settings")
def settings(s: SettingsIn, u: User = Depends(me), db: Session = Depends(get_db)):
    try: ZoneInfo(s.tz)
    except Exception: raise HTTPException(400, "Unknown timezone")
    u.start_override, u.end_override, u.tz, u.currency = s.period_start_override, s.period_end_override, s.tz, s.currency
    st, _, en, _ = resolve_period(u, datetime.now(ZoneInfo(s.tz)).date())
    if st > en: raise HTTPException(400, "Period Starting Date must be on or before Period Ending Date")
    db.merge(u); db.commit(); return {"ok": True}


class CatIn(BaseModel):
    kind: str = Field(pattern="^(income|expense)$")
    name: str = Field(min_length=1, max_length=60)


@app.post("/api/categories")
def add_cat(c: CatIn, u: User = Depends(me), db: Session = Depends(get_db)):
    name = c.name.strip()
    if any(x.lower() == name.lower() for x in db.scalars(select(Cat.name).where(Cat.user_id == u.id, Cat.kind == c.kind))):
        raise HTTPException(409, "Category exists")
    db.add(Cat(user_id=u.id, kind=c.kind, name=name)); db.commit(); return {"ok": True}


@app.delete("/api/categories/{cid}")
def del_cat(cid: int, u: User = Depends(me), db: Session = Depends(get_db)):
    c = db.scalar(select(Cat).where(Cat.id == cid, Cat.user_id == u.id))
    if not c: raise HTTPException(404)
    if db.scalar(select(Txn.id).where(Txn.user_id == u.id, (Txn.income_cat_id == cid) | (Txn.expense_cat_id == cid)).limit(1)):
        raise HTTPException(409, "Category is used by transactions")
    db.delete(c); db.commit(); return {"ok": True}


# ---------- transactions ----------
class TxnIn(BaseModel):
    date: Date
    income_cat_id: Optional[int] = None
    expense_cat_id: Optional[int] = None
    description: str = Field("", max_length=200)
    amount: str = Field(max_length=100)


def check(t: TxnIn, u: User, db: Session):
    try: cents = to_cents(t.amount)
    except ValueError: raise HTTPException(400, "Invalid amount")
    if not (t.income_cat_id or t.expense_cat_id): raise HTTPException(400, "Choose an income and/or expense type")
    for cid, kind in ((t.income_cat_id, "income"), (t.expense_cat_id, "expense")):
        if cid and not db.scalar(select(Cat.id).where(Cat.id == cid, Cat.user_id == u.id, Cat.kind == kind)):
            raise HTTPException(400, "Unknown category")  # cannot reference another user's category
    return cents, (t.amount.strip().lstrip("=") if not re.fullmatch(r"-?\d+(\.\d+)?", t.amount.strip()) else None)


@app.get("/api/transactions")
def txns(scope: str = "current", date_from: Optional[Date] = None, date_to: Optional[Date] = None, u: User = Depends(me), db: Session = Depends(get_db)):
    cats, rows, week, inp, _ = compute(u, db)
    nm = {c.id: c.name for c in cats}
    if date_from and date_to and date_from > date_to: raise HTTPException(400, "From date must be on or before To date")
    if date_from or date_to:  # explicit date range (inclusive), may lie outside the current period
        keep = lambda t: (not date_from or t.date >= date_from) and (not date_to or t.date <= date_to)
    else: keep = lambda t: scope == "all" or inp(t)
    out = [{"id": t.id, "date": str(t.date), "income_type": nm.get(t.income_cat_id), "income_cat_id": t.income_cat_id,
            "expense_type": nm.get(t.expense_cat_id), "expense_cat_id": t.expense_cat_id, "description": t.description,
            "amount": money(t.amount), "expr": t.expr, "current": inp(t), "week": f"Week {week[t.id]}" if t.id in week else ""}
           for t in rows if keep(t)]
    return out[::-1]


@app.post("/api/transactions")
def add_txn(t: TxnIn, u: User = Depends(me), db: Session = Depends(get_db)):
    cents, expr = check(t, u, db)
    db.add(Txn(user_id=u.id, date=t.date, income_cat_id=t.income_cat_id, expense_cat_id=t.expense_cat_id,
               description=t.description.strip(), amount=cents, expr=expr)); db.commit(); return {"ok": True}


@app.put("/api/transactions/{tid}")
def edit_txn(tid: int, t: TxnIn, u: User = Depends(me), db: Session = Depends(get_db)):
    row = db.scalar(select(Txn).where(Txn.id == tid, Txn.user_id == u.id))
    if not row: raise HTTPException(404)
    cents, expr = check(t, u, db)
    row.date, row.income_cat_id, row.expense_cat_id = t.date, t.income_cat_id, t.expense_cat_id
    row.description, row.amount, row.expr = t.description.strip(), cents, expr
    db.commit(); return {"ok": True}


@app.delete("/api/transactions/{tid}")
def del_txn(tid: int, u: User = Depends(me), db: Session = Depends(get_db)):
    row = db.scalar(select(Txn).where(Txn.id == tid, Txn.user_id == u.id))
    if not row: raise HTTPException(404)
    db.delete(row); db.commit(); return {"ok": True}


@app.get("/api/summary")
def summary(u: User = Depends(me), db: Session = Depends(get_db)):
    cats, _, _, _, s = compute(u, db)
    return {**s, "categories": [{"id": c.id, "kind": c.kind, "name": c.name} for c in cats]}


# ---------- charts ----------
@app.get("/api/charts/monthly-expenses")
def monthly_expenses(category_id: Optional[int] = None, u: User = Depends(me), db: Session = Depends(get_db)):
    """Expense totals per calendar month (all recorded transactions), optionally for one expense category."""
    q = select(Txn.date, Txn.amount).where(Txn.user_id == u.id, Txn.expense_cat_id.is_not(None))
    if category_id is not None:
        if not db.scalar(select(Cat.id).where(Cat.id == category_id, Cat.user_id == u.id, Cat.kind == "expense")):
            raise HTTPException(404)
        q = q.where(Txn.expense_cat_id == category_id)
    sums: dict = {}
    for d, a in db.execute(q):
        sums[(d.year, d.month)] = sums.get((d.year, d.month), 0) + a
    if not sums: return []
    (y, mo), last, out = min(sums), max(sums), []
    while (y, mo) <= last:  # include months with no spending as 0
        out.append({"month": f"{y}-{mo:02d}", "total": money(sums.get((y, mo), 0))})
        y, mo = y + (mo == 12), mo % 12 + 1
    return out[-60:]


# ---------- CSV import (Transactions columns only) ----------
HEAD = ["Date", "Income Type", "Exp Type", "Description", "Amount"]
ALIAS = {"date": 0, "income type": 1, "income": 1, "exp type": 2, "expense type": 2, "expense": 2,
         "description": 3, "desc": 3, "amount": 4}
NOTE_ROW = ["Date format: YYYY-MM-DD, remove this row before uploading the file", "", "", "", ""]


@app.get("/api/import/template")
def import_template():
    import io as _io, csv as _csv
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\r\n")
    w.writerow(HEAD)
    w.writerow(NOTE_ROW)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="import_template.csv"'})

def parse_dates(raw):
    iso, sl = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})"), re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})")
    nums = [(int(x[1]), int(x[2])) for x in map(sl.match, raw) if x]
    dayfirst = None
    if any(a > 12 for a, b in nums): dayfirst = True
    if any(b > 12 for a, b in nums):
        if dayfirst: raise ValueError("Dates mix day-first and month-first formats. Use YYYY-MM-DD.")
        dayfirst = False
    if nums and dayfirst is None: raise ValueError("Dates like 03/04/2026 are ambiguous. Use YYYY-MM-DD.")
    out = []
    for s in raw:
        try:
            if iso.match(s): y, mo, d = map(int, iso.match(s).groups())
            elif sl.match(s):
                a, b, y = map(int, sl.match(s).groups()); d, mo = (a, b) if dayfirst else (b, a)
            else: out.append(None); continue
            out.append(Date(y, mo, d))
        except ValueError: out.append(None)
    return out


@app.post("/api/import")
async def import_csv(file: UploadFile = File(...), u: User = Depends(me), db: Session = Depends(get_db)):
    data = await file.read()
    if len(data) > 2_000_000: raise HTTPException(413, "File too large (max 2 MB)")
    try: text = data.decode("utf-8-sig")
    except UnicodeDecodeError: text = data.decode("cp1252")
    rows = [(n, r) for n, r in enumerate(csv.reader(io.StringIO(text)), 1) if any(c.strip() for c in r)]
    if not rows: raise HTTPException(400, "The file is empty")
    idx = {ALIAS[h]: i for i, h in enumerate(c.strip().lower().replace("_", " ") for c in rows[0][1]) if h in ALIAS}
    if 0 not in idx or 4 not in idx:
        raise HTTPException(400, "Header row must include Date and Amount (columns: " + ", ".join(HEAD) + ")")
    body = rows[1:]
    if len(body) > 5000: raise HTTPException(400, "Too many rows (max 5000 per file)")
    get = lambda r, k: r[idx[k]].strip() if k in idx and idx[k] < len(r) else ""
    try: dates = parse_dates([get(r, 0) for _, r in body])
    except ValueError as e: raise HTTPException(400, str(e))
    errs, parsed = [], []
    for (n, r), d in zip(body, dates):
        inc, exp, desc, amt = get(r, 1), get(r, 2), get(r, 3), get(r, 4).replace(",", "").replace(" ", "")
        if re.fullmatch(r"\(\d+(\.\d+)?\)", amt): amt = "-" + amt[1:-1]
        try: cents = to_cents(amt)
        except ValueError: cents = None
        if d is None: errs.append(f"Line {n}: invalid date")
        elif cents is None: errs.append(f"Line {n}: invalid amount")
        elif not (inc or exp): errs.append(f"Line {n}: needs an Income Type and/or Exp Type")
        elif len(inc) > 60 or len(exp) > 60: errs.append(f"Line {n}: type name longer than 60 characters")
        else: parsed.append((d, inc, exp, desc[:200], cents))
    if errs:  # all-or-nothing: nothing is saved if any row is invalid
        raise HTTPException(400, "No rows imported.\n" + "\n".join(errs[:10]) + (f"\n...and {len(errs) - 10} more" if len(errs) > 10 else ""))
    cats = {(c.kind, c.name.lower()): c for c in db.scalars(select(Cat).where(Cat.user_id == u.id))}

    def cat(kind, name):
        k = (kind, name.lower())
        if k not in cats:
            cats[k] = Cat(user_id=u.id, kind=kind, name=name); db.add(cats[k]); db.flush()
        return cats[k].id
    for d, inc, exp, desc, cents in parsed:
        db.add(Txn(user_id=u.id, date=d, income_cat_id=cat("income", inc) if inc else None,
                   expense_cat_id=cat("expense", exp) if exp else None, description=desc, amount=cents))
    db.commit(); return {"imported": len(parsed)}


STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index(): return FileResponse(STATIC / "index.html")


ICON_DIR = Path(__file__).parent / "assets" / "svg"


@app.get("/favicon.svg", include_in_schema=False)
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    svgs = sorted(ICON_DIR.glob("*.svg"), key=lambda p: (p.name != "favicon.svg", p.name))
    if not svgs: return Response(status_code=204)
    return FileResponse(svgs[0], media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})