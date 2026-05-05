import os
import base64
from datetime import datetime

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import database
import parser as xlsx_parser

app = FastAPI(title="Organic Market Dashboard")
templates = Jinja2Templates(directory="templates")

SECRET_KEY        = os.environ.get("SECRET_KEY",         "skift-mig-i-railway-variables")
WEBHOOK_SECRET    = os.environ.get("WEBHOOK_SECRET",     "OM-Greve-2026-Hemlig")
DASHBOARD_USER    = os.environ.get("DASHBOARD_USERNAME", "linda")
DASHBOARD_PASS    = os.environ.get("DASHBOARD_PASSWORD", "")

signer = URLSafeTimedSerializer(SECRET_KEY)
SESSION_MAX_AGE   = 60 * 60 * 24 * 7  # 7 dage


# ── SESSION ───────────────────────────────────────────────────────────────────

def get_session(request: Request):
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        return signer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# ── STARTUP ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    database.init_db()


# ── SIDER ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not get_session(request):
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_side(request: Request):
    if get_session(request):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "fejl": None})


@app.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    brugernavn: str = Form(...),
    adgangskode: str = Form(...),
):
    if brugernavn == DASHBOARD_USER and adgangskode == DASHBOARD_PASS:
        token = signer.dumps({"brugernavn": brugernavn})
        svar = RedirectResponse("/dashboard", status_code=303)
        svar.set_cookie("session", token, httponly=True, samesite="lax", max_age=SESSION_MAX_AGE)
        return svar
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "fejl": "Forkert brugernavn eller adgangskode"},
    )


@app.get("/logout")
async def logout():
    svar = RedirectResponse("/login", status_code=302)
    svar.delete_cookie("session")
    return svar


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not get_session(request):
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ── API ───────────────────────────────────────────────────────────────────────

def _kræv_login(request: Request):
    if not get_session(request):
        raise HTTPException(status_code=401, detail="Ikke logget ind")


@app.get("/api/data")
async def api_data(request: Request):
    _kræv_login(request)
    return database.hent_dashboard_data()


@app.get("/api/kpi")
async def api_kpi(request: Request):
    _kræv_login(request)
    return database.hent_kpi()


@app.get("/api/salg/dage")
async def api_dage(request: Request, n: int = 14):
    _kræv_login(request)
    return database.hent_dage(min(n, 365))


@app.get("/api/salg/uger")
async def api_uger(request: Request):
    _kræv_login(request)
    return database.hent_uger()


@app.get("/api/salg/timer")
async def api_timer(request: Request):
    _kræv_login(request)
    return database.hent_timer_idag()


@app.get("/api/salg/timer/snit")
async def api_timer_snit(request: Request):
    _kræv_login(request)
    return database.hent_timer_snit()


@app.get("/api/salg/kategorier")
async def api_kategorier(request: Request):
    _kræv_login(request)
    return database.hent_kategorier()


@app.get("/api/salg/top")
async def api_top(request: Request, n: int = 20):
    _kræv_login(request)
    return database.hent_top_produkter(min(n, 100))


@app.get("/api/salg/dage-detaljer")
async def api_dage_detaljer(request: Request, n: int = 8):
    _kræv_login(request)
    return database.hent_dage_detaljer(min(n, 30))


@app.get("/api/rapport-status")
async def rapport_status():
    info = database.hent_seneste_snapshot_info()
    return {
        "ok": True,
        "seneste_rapport": info.get("rapport_dato") if info else None,
        "indlæst":         info.get("indlæst_dato") if info else None,
    }


# ── WEBHOOK ───────────────────────────────────────────────────────────────────

@app.post("/api/opdater-rapport")
async def opdater_rapport(request: Request):
    header_secret = request.headers.get("X-Webhook-Secret", "")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Ugyldig JSON")

    if header_secret != WEBHOOK_SECRET and body.get("secret") != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Ugyldig webhook secret")

    encoded = body.get("data")
    if not encoded:
        raise HTTPException(status_code=400, detail="Ingen fil-data")

    dato_str = body.get("dato", datetime.now().isoformat())
    rapport_dato = dato_str[:10]

    try:
        fil_bytes     = base64.b64decode(encoded)
        transaktioner = xlsx_parser.parse_shopbox_xlsx(fil_bytes)

        if not transaktioner:
            raise HTTPException(status_code=422, detail="Ingen transaktioner fundet i filen")

        upload_id = database.gem_transaktioner(rapport_dato, transaktioner)
        return {"ok": True, "upload_id": upload_id, "rækker": len(transaktioner), "dato": rapport_dato}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fejl ved behandling: {str(e)}")
