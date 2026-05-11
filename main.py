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
async def api_kpi(request: Request, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_kpi(aar)


@app.get("/api/salg/idag")
async def api_idag(request: Request, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_dag_produkter(aar)


@app.get("/api/salg/dage")
async def api_dage(request: Request, n: int = 14, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_dage(min(n, 365), aar)


@app.get("/api/salg/uger")
async def api_uger(request: Request, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_uger(aar)


@app.get("/api/salg/timer")
async def api_timer(request: Request, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_timer_idag(aar)


@app.get("/api/salg/timer/snit")
async def api_timer_snit(request: Request, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_timer_snit(aar)


@app.get("/api/salg/kategorier")
async def api_kategorier(request: Request, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_kategorier(aar)


@app.get("/api/salg/top")
async def api_top(request: Request, n: int = 20, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_top_produkter(min(n, 100), aar)


@app.get("/api/salg/aarsdata")
async def api_aarsdata(request: Request, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_aarsdata(aar)


@app.get("/api/salg/trend")
async def api_trend(request: Request, dage: int = 21, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_trend_analyse(min(dage, 90), aar)


@app.get("/api/salg/kaffe")
async def api_kaffe(request: Request, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_kaffe_analyse(aar)


@app.get("/api/salg/dage-detaljer")
async def api_dage_detaljer(request: Request, n: int = 8, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_dage_detaljer(min(n, 30), aar)


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
async def api_bestilling_uger(request: Request, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_bestilling_uger(aar)


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
async def api_bager_svind(request: Request, aar: Optional[int] = None):
    _kræv_login(request)
    return database.hent_svind_data(aar)


@app.post("/api/bestilling/gem-manuel")
async def bestilling_gem_manuel(request: Request):
    _kræv_login(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Ugyldig JSON")
    uge  = body.get("uge")
    aar  = body.get("aar")
    vn   = body.get("varenummer")
    dag  = body.get("dag")
    antal = body.get("antal")
    if uge is None or aar is None or vn is None or dag is None or antal is None:
        raise HTTPException(status_code=400, detail="Mangler felter")
    database.gem_bestilling_manuel(int(uge), int(aar), str(vn), str(dag), int(antal))
    return {"ok": True}


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
    from openpyxl.styles import Font, PatternFill, Alignment

    DAGE    = ['man', 'tir', 'ons', 'tor', 'fre', 'loe', 'son']
    DAG_LBL = ['Mandag', 'Tirsdag', 'Onsdag', 'Torsdag', 'Fredag', 'Lørdag', 'Søndag']
    HDR_BG  = "FFC4D79B"

    wb = Workbook()
    ws = wb.active
    ws.title = "Ark1"

    # Kolonnebredder fra skabelonen
    ws.column_dimensions['A'].width = 18.86
    ws.column_dimensions['B'].width = 10.71
    ws.column_dimensions['C'].width = 33.14
    ws.column_dimensions['D'].width = 16.57
    for col in 'EFGHIJK':
        ws.column_dimensions[col].width = 8.86
    ws.column_dimensions['L'].width = 10.71
    ws.column_dimensions['M'].width = 23.86

    hdr_fill = PatternFill("solid", fgColor=HDR_BG)

    def hdr_row(row_num):
        for cell in ws[row_num]:
            cell.fill = hdr_fill

    # Rad 1: "Organic Market" (A bold) + titel i C (bold)
    evt_txt = f"  ·  {d['event']['navn']}" if d.get("event") else ""
    titel = (f"Bestilling uge {d['maal_uge']} {d['maal_aar']}  ·  {d['dato_range']}"
             f"  ·  SI {d['si']:.2f}{evt_txt}  ·  basis uge {d['basis_uge']} {d['basis_aar']}")
    ws.append(["Organic Market", None, titel] + [None] * 12)
    ws["A1"].font = Font(bold=True)
    ws["C1"].font = Font(bold=True)
    hdr_row(1)
    ws.row_dimensions[1].height = 14.25

    # Rad 2: "Uge X" i A + kolonneoverskrifter
    ws.append([f"Uge {d['maal_uge']}", "Varenummer", "VARETYPE", "Pris ex moms"]
              + DAG_LBL + ["Total antal", "Pris ex moms", None, None])
    for cell in ws[2]:
        cell.fill = hdr_fill
        cell.font = Font(bold=True)
    ws.row_dimensions[2].height = 14.25

    # Rad 3: blanke/spaces i dagkolonnerne
    ws.append([None, None, None, None] + [" "] * 7 + [None, None, None, None])
    hdr_row(3)
    ws.row_dimensions[3].height = 14.25

    ws.freeze_panes = "C4"

    # Opdel produkter i tre sektioner (bevar original rækkefølge)
    sek1, sek2, sek3 = [], [], []
    for p in d["produkter"]:
        kat  = p.get("kategori", "")
        navn = p["varenavn"].lower()
        if kat != "Kage":
            sek1.append(p)
        elif "muffin" in navn or "brownie" in navn:
            sek3.append(p)
        else:
            sek2.append(p)

    def _v(val):
        return None if (val == 0 or val is None) else val

    def skriv_produkt(p):
        anb = p["anbefalet"]
        ws.append([None, p["varenummer"], p["varenavn"], p["pris_ex_moms"]]
                  + [_v(anb[dg]) for dg in DAGE]
                  + [_v(p["total_anbefalet"]), p["total_pris"] or None, None, None])
        ws.row_dimensions[ws.max_row].height = 15.75

    def skriv_ialt(produkter):
        dag_sums = [sum(p["anbefalet"][dg] for p in produkter) for dg in DAGE]
        ws.append([None, None, "I alt", None]
                  + [_v(s) for s in dag_sums]
                  + [None, None, None, None])
        r = ws.max_row
        for cell in ws[r]:
            cell.font = Font(bold=True)
        ws.row_dimensions[r].height = 15.75

    def blank():
        ws.append([None] * 15)
        ws.row_dimensions[ws.max_row].height = 15.75

    for p in sek1:
        skriv_produkt(p)
    skriv_ialt(sek1)

    blank(); blank()

    for p in sek2:
        skriv_produkt(p)
    if sek2:
        skriv_ialt(sek2)

    blank(); blank()

    for p in sek3:
        skriv_produkt(p)
    if sek3:
        skriv_ialt(sek3)

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


@app.get("/api/mobilepay")
async def api_mobilepay(request: Request):
    _kræv_login(request)
    return database.hent_mobilepay()


@app.post("/api/mobilepay/gem")
async def mobilepay_gem(request: Request):
    _kræv_login(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Ugyldig JSON")
    aar       = body.get("aar")
    maaned    = body.get("maaned")
    omsaetning = body.get("omsaetning")
    if aar is None or maaned is None or omsaetning is None:
        raise HTTPException(status_code=400, detail="Mangler felter")
    database.gem_mobilepay(int(aar), int(maaned), float(omsaetning))
    return {"ok": True}


@app.get("/api/salg/mangler-kostpris")
async def api_mangler_kostpris(request: Request):
    _kræv_login(request)
    return database.hent_mangler_kostpris()


# ── VARESTAMDATA ──────────────────────────────────────────────────────────────

@app.get("/api/stamdata")
async def api_stamdata(request: Request):
    _kræv_login(request)
    return database.hent_stamdata()


@app.post("/api/stamdata/gem")
async def stamdata_gem(request: Request):
    _kræv_login(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Ugyldig JSON")
    varenavn = body.get("varenavn", "").strip()
    type_    = body.get("type", "").strip()
    if not varenavn or not type_:
        raise HTTPException(status_code=400, detail="Mangler varenavn eller type")
    id_ = database.gem_stamdata_linje(
        body.get("sku", ""),
        varenavn,
        type_,
        float(body.get("pris_ex_moms", 0) or 0),
    )
    return {"ok": True, "id": id_}


@app.delete("/api/stamdata/{id_}")
async def stamdata_slet(request: Request, id_: int):
    _kræv_login(request)
    database.slet_stamdata(id_)
    return {"ok": True}


@app.post("/api/stamdata/bulk")
async def stamdata_bulk(request: Request):
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
    antal = database.gem_stamdata_bulk(linjer)
    return {"ok": True, "linjer": antal}


@app.get("/api/aarsplan/vf-detaljer")
async def api_vf_detaljer(request: Request, aar: int, maaned: int):
    _kræv_login(request)
    return database.hent_vf_detaljer(aar, maaned)


@app.get("/api/faste-omk")
async def api_faste_omk(request: Request, aar: int):
    _kræv_login(request)
    return {"items": database.hent_faste_omk(aar)}


@app.post("/api/faste-omk/gem")
async def faste_omk_gem(request: Request):
    _kræv_login(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Ugyldig JSON")
    database.gem_faste_omk(
        int(body["aar"]),
        int(body["maaned"]),
        str(body["kategori"]),
        float(body["beloeb"]),
    )
    return {"ok": True}


@app.post("/api/faste-omk/slet-kategori")
async def faste_omk_slet(request: Request):
    _kræv_login(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Ugyldig JSON")
    database.slet_faste_omk_kategori(int(body["aar"]), str(body["kategori"]))
    return {"ok": True}


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
