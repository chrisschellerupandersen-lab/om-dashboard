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
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
    from collections import defaultdict

    DAGE     = ['man', 'tir', 'ons', 'tor', 'fre', 'loe', 'son']
    DAG_LBL  = ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn']
    KAT_ORD  = ['Rugbrød', 'Flute', 'Brød', 'Boller', 'Wiener', 'Kage']
    WEEKEND  = {4, 5, 6}  # 0-based: fre=4, loe=5, son=6

    wb = Workbook()
    ws = wb.active
    ws.title = f"Uge {d['maal_uge']}"

    # Kolonne-bredder
    col_widths = [14, 10, 32, 12, 7, 7, 7, 7, 7, 7, 7, 10, 12]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w

    forest  = "FF1E3A1E"
    parch   = "FFF5F0E8"
    green_l = "FFE8F0E8"
    gold    = "FFB8860B"
    white   = "FFFFFFFF"
    grey_l  = "FFF0EDE8"

    def _fill(hex_col):
        return PatternFill("solid", fgColor=hex_col)

    def _font(bold=False, color="FF000000", sz=10):
        return Font(bold=bold, color=color, size=sz)

    def _border_bottom():
        s = Side(style="thin", color="FF999999")
        return Border(bottom=s)

    # ── Rad 1: Titel ──────────────────────────────────────────────────────────
    ws.append([f"Organic Market — Ugebestilling uge {d['maal_uge']} · {d['maal_aar']}"])
    ws.merge_cells("A1:M1")
    c = ws["A1"]
    c.font      = Font(bold=True, size=13, color=white)
    c.fill      = _fill(forest)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 22

    # ── Rad 2: Dato-range + faktorer ─────────────────────────────────────────
    evt_txt = f"  ·  {d['event']['navn']}" if d.get("event") else ""
    ws.append([f"{d['dato_range']}  ·  SI {d['si']:.2f}  ·  vækst {d['vaekst_pct']:+.1f}%{evt_txt}"])
    ws.merge_cells("A2:M2")
    c = ws["A2"]
    c.font      = Font(size=9, italic=True, color="FF555555")
    c.fill      = _fill(parch)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[2].height = 14

    # ── Rad 3: Tom (matcher parser skip i<3) ─────────────────────────────────
    ws.append([None] * 13)
    ws.row_dimensions[3].height = 4

    # ── Rad 4: Kolonneoverskrifter ────────────────────────────────────────────
    hdrs = ["Varetype", "Varenr.", "Varenavn", "Pris ex moms"] + DAG_LBL + ["I alt stk", "I alt kr"]
    ws.append(hdrs)
    for col_i, cell in enumerate(ws[4], 1):
        cell.font      = Font(bold=True, size=9, color=white)
        cell.fill      = _fill(forest)
        cell.alignment = Alignment(horizontal="center" if col_i > 2 else "left", vertical="center")
        if col_i in WEEKEND:
            cell.fill = _fill("FF2D4A2D")
    ws.row_dimensions[4].height = 16

    # ── Produkter grupperet efter kategori ───────────────────────────────────
    groups: dict = defaultdict(list)
    for p in d["produkter"]:
        groups[p["kategori"]].append(p)

    for kat in KAT_ORD:
        prods = groups.get(kat, [])
        if not prods:
            continue

        # Kategori-header
        ws.append([kat] + [None] * 12)
        r = ws.max_row
        for col_i in range(1, 14):
            c = ws.cell(r, col_i)
            c.fill = _fill(green_l)
            c.font = Font(bold=True, size=9, color=forest)
        ws.row_dimensions[r].height = 14

        for p in prods:
            anb = p["anbefalet"]
            dag_vals = [anb[dg] for dg in DAGE]
            ws.append([None, p["varenummer"], p["varenavn"], p["pris_ex_moms"]]
                      + dag_vals + [p["total_anbefalet"], round(p["total_pris"])])
            r = ws.max_row
            for col_i, cell in enumerate(ws[r], 1):
                cell.font = Font(size=9)
                cell.alignment = Alignment(horizontal="right" if col_i > 3 else "left",
                                           vertical="center")
                if col_i == 3:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                if col_i - 1 in WEEKEND and col_i >= 5:
                    cell.fill = _fill("FFEFF5EF")
            ws.row_dimensions[r].height = 13

        # Kategori-subtotal
        kat_dag = [sum(p["anbefalet"][dg] for p in prods) for dg in DAGE]
        kat_stk = sum(p["total_anbefalet"] for p in prods)
        kat_kr  = sum(p["total_pris"]      for p in prods)
        ws.append([f"{kat} i alt", None, None, None] + kat_dag + [kat_stk, round(kat_kr)])
        r = ws.max_row
        for col_i, cell in enumerate(ws[r], 1):
            cell.font   = Font(bold=True, size=9, color=gold)
            cell.fill   = _fill(grey_l)
            cell.border = _border_bottom()
            cell.alignment = Alignment(horizontal="right" if col_i > 1 else "left",
                                       vertical="center")
        ws.row_dimensions[r].height = 13

    # ── Grand total ──────────────────────────────────────────────────────────
    all_dag = [sum(p["anbefalet"][dg] for p in d["produkter"]) for dg in DAGE]
    ws.append(["I alt", None, None, None] + all_dag + [d["total_stk"], round(d["total_kr"])])
    r = ws.max_row
    for col_i, cell in enumerate(ws[r], 1):
        cell.font      = Font(bold=True, size=10, color=white)
        cell.fill      = _fill(forest)
        cell.alignment = Alignment(horizontal="right" if col_i > 1 else "left",
                                   vertical="center")
    ws.row_dimensions[r].height = 16

    # Frys øverste 4 rækker
    ws.freeze_panes = "A5"

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
