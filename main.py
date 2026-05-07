import io
import os
import base64
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
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


@app.get("/api/salg/idag")
async def api_idag(request: Request):
    _kræv_login(request)
    return database.hent_dag_produkter()


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


@app.get("/api/salg/aarsdata")
async def api_aarsdata(request: Request, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_aarsdata(aar)


@app.get("/api/salg/trend")
async def api_trend(request: Request, dage: int = 21):
    _kræv_login(request)
    return database.hent_trend_analyse(min(dage, 90))


@app.get("/api/salg/kaffe")
async def api_kaffe(request: Request):
    _kræv_login(request)
    return database.hent_kaffe_analyse()


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

@app.get("/api/bestilling/uger")
async def api_bestilling_uger(request: Request):
    _kræv_login(request)
    return database.hent_bestilling_uger()


@app.get("/api/bestilling/uge/{uge}")
async def api_bestilling_uge(request: Request, uge: int, aar: Optional[int] = None):
    _kræv_login(request)
    if aar is None:
        from datetime import datetime
        aar = datetime.now().year
    return database.hent_bestilling_uge(uge, aar)


@app.post("/api/bestilling/opdater")
async def bestilling_opdater(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Ugyldig JSON")

    header_secret = request.headers.get("X-Webhook-Secret", "")
    if header_secret != WEBHOOK_SECRET and body.get("secret") != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Ugyldig webhook secret")

    uge    = body.get("uge")
    aar    = body.get("aar")
    linjer = body.get("linjer", [])

    if not uge or not aar:
        raise HTTPException(status_code=400, detail="Mangler uge eller aar")
    if not linjer:
        raise HTTPException(status_code=400, detail="Ingen bestillingslinjer")

    antal = database.gem_ugebestilling(int(uge), int(aar), linjer)
    return {"ok": True, "uge": uge, "aar": aar, "linjer": antal}


@app.get("/api/bager/svind")
async def api_bager_svind(request: Request):
    _kræv_login(request)
    return database.hent_svind_data()


@app.get("/api/bestilling/anbefaling")
async def api_bestillings_anbefaling(
    request: Request,
    uge: Optional[int] = None,
    aar: Optional[int] = None,
):
    _kræv_login(request)
    if uge is None:
        from datetime import date
        iso = date.today().isocalendar()
        uge = iso[1] + 1
        aar = iso[0]
        if uge > 52:
            uge = 1
            aar += 1
    if aar is None:
        from datetime import date
        aar = date.today().year
    return database.hent_bestillings_uge(int(uge), int(aar))


@app.get("/api/bestilling/eksport")
async def api_bestilling_eksport(
    request: Request,
    uge: Optional[int] = None,
    aar: Optional[int] = None,
):
    _kræv_login(request)
    if uge is None:
        from datetime import date
        iso = date.today().isocalendar()
        uge = iso[1] + 1
        aar = iso[0]
        if uge > 52:
            uge = 1
            aar += 1
    if aar is None:
        from datetime import date
        aar = date.today().year

    d = database.hent_bestillings_uge(int(uge), int(aar))
    if "fejl" in d:
        raise HTTPException(status_code=404, detail=d["fejl"])

    xlsx_bytes = _byg_bestilling_xlsx(d)
    filename = f"Bestilling uge {d['maal_uge']} {d['maal_aar']}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _byg_bestilling_xlsx(d: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    DAGE    = ['man', 'tir', 'ons', 'tor', 'fre', 'loe', 'son']
    DAG_LBL = ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn']
    # Openpyxl-kolonnenumre (1-baseret) for Fre/Lør/Søn
    WEEKEND_COL = {9, 10, 11}

    # Kategori-farver — subtile baggrunde så man kan se sektionerne
    KAT_COLOR = {
        'Rugbrød': 'FFFFF3CC', 'Flute': 'FFFFF3CC',
        'Brød':    'FFFFE8D0',
        'Boller':  'FFE8F4E8',
        'Wiener':  'FFE8EEFF',
        'Kage':    'FFFFF0F5',
    }
    DEFAULT_COLOR = 'FFFFFFFF'

    wb = Workbook()
    ws = wb.active
    ws.title = f"Uge {d['maal_uge']}"

    for col_ltr, w in zip('ABCDEFGHIJKLM', [16, 10, 36, 12, 7, 7, 7, 7, 8, 8, 8, 10, 12]):
        ws.column_dimensions[col_ltr].width = w

    grey    = "FFD9D9D9"
    grey_dk = "FFB8B8B8"

    def fill(c):  return PatternFill("solid", fgColor=c)
    def bd_top(): return Border(top=Side(style="thin", color="FF999999"))
    def bd_bot(): return Border(bottom=Side(style="thin", color="FF999999"))

    # ── Rad 1 (i=0): Titel ───────────────────────────────────────────────────
    ws.append([f"Organic Market  –  Ugebestilling uge {d['maal_uge']}  ·  {d['maal_aar']}  ·  {d['dato_range']}"]
              + [None] * 12)
    ws.merge_cells("A1:M1")
    ws["A1"].font      = Font(bold=True, size=12)
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 20

    # ── Rad 2 (i=1): faktorer ────────────────────────────────────────────────
    evt_txt = f"  ·  {d['event']['navn']}" if d.get("event") else ""
    ws.append([f"SI {d['si']:.2f}  ·  vækst {d['vaekst_pct']:+.1f}%{evt_txt}"
               f"  ·  basis: uge {d['basis_uge']} {d['basis_aar']}"]
              + [None] * 12)
    ws.merge_cells("A2:M2")
    ws["A2"].font      = Font(italic=True, size=9, color="FF666666")
    ws["A2"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[2].height = 14

    # ── Rad 3 (i=2): Kolonneoverskrifter ─────────────────────────────────────
    hdrs = ["Varetype", "Varenr.", "Varenavn", "Pris ex moms"] + DAG_LBL + ["I alt stk", "I alt kr"]
    ws.append(hdrs)
    for ci, cell in enumerate(ws[3], 1):
        cell.font      = Font(bold=True, size=9)
        cell.fill      = fill(grey)
        cell.alignment = Alignment(horizontal="center" if ci > 2 else "left", vertical="center")
        cell.border    = bd_bot()
        if ci in WEEKEND_COL:
            cell.fill = fill(grey_dk)
    ws.row_dimensions[3].height = 15
    ws.freeze_panes = "A4"

    # ── Produkter i original rækkefølge (i=3+) ───────────────────────────────
    # Ingen kategori-omsortering — produkterne er allerede i original
    # rækkefølge fra basen. Ny kategori → let skillelinje øverst.
    prev_kat = None
    for p in d["produkter"]:
        kat  = p.get("kategori", "")
        anb  = p["anbefalet"]
        bg   = KAT_COLOR.get(kat, DEFAULT_COLOR)
        # Weekend-celler får lidt mørkere grøn variant af kategorifarven
        wknd_bg = "FFD4EDD4" if kat in ("Boller", "Wiener", "Brød") else "FFE8E8D8"

        dag_vals = [anb[dg] for dg in DAGE]
        ws.append([kat if kat != prev_kat else None,
                   p["varenummer"], p["varenavn"], p["pris_ex_moms"]]
                  + dag_vals + [p["total_anbefalet"], p["total_pris"]])
        r = ws.max_row

        for ci, cell in enumerate(ws[r], 1):
            cell.font      = Font(size=9)
            cell.fill      = fill(bg)
            cell.alignment = Alignment(
                horizontal="right" if ci > 3 else "left", vertical="center")
            if ci in WEEKEND_COL:
                cell.fill = fill(wknd_bg)

        # Tynd skillelinje øverst ved ny kategori
        if kat != prev_kat and prev_kat is not None:
            for ci in range(1, 14):
                ws.cell(r, ci).border = bd_top()

        ws.row_dimensions[r].height = 13
        prev_kat = kat

    # ── Grand total ───────────────────────────────────────────────────────────
    all_dag = [sum(p["anbefalet"][dg] for p in d["produkter"]) for dg in DAGE]
    ws.append(["I alt"] + [None, None, None] + all_dag
              + [d["total_stk"], round(d["total_kr"], 2)])
    r = ws.max_row
    for ci, cell in enumerate(ws[r], 1):
        cell.font      = Font(bold=True, size=9)
        cell.fill      = fill(grey)
        cell.border    = bd_top()
        cell.alignment = Alignment(
            horizontal="right" if ci > 1 else "left", vertical="center")
    ws.row_dimensions[r].height = 15

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@app.post("/api/bager/retur-opdater")
async def bager_retur_opdater(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Ugyldig JSON")
    header_secret = request.headers.get("X-Webhook-Secret", "")
    if header_secret != WEBHOOK_SECRET and body.get("secret") != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Ugyldig webhook secret")
    linjer = body.get("linjer", [])
    if not linjer:
        raise HTTPException(status_code=400, detail="Ingen linjer")
    antal = database.gem_bager_regnskab(linjer)
    return {"ok": True, "linjer": antal}


@app.get("/api/salg/mangler-kostpris")
async def api_mangler_kostpris(request: Request):
    _kræv_login(request)
    return database.hent_mangler_kostpris()


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
