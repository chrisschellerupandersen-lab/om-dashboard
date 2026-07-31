import os
import json
import base64
import hashlib
import logging
import secrets
from datetime import date, timedelta
from contextlib import asynccontextmanager
from typing import Optional

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import database
import enable_banking_klient

logger = logging.getLogger("regnskab.bank_sync")

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")

_scheduler = BackgroundScheduler(timezone="Europe/Copenhagen")


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    database.init_db()
    _scheduler.add_job(_synkroniser_bankforbindelser, "cron", hour=6, minute=0, id="bank_sync", replace_existing=True)
    _scheduler.start()
    yield
    _scheduler.shutdown()


app = FastAPI(title="Regnskab", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
templates.env.globals["bruger_fra_session"] = lambda request: (get_session(request) or {}).get("brugernavn")


def _dkk(value) -> str:
    """Dansk talformat: punktum som tusindtalsseparator, komma som decimal — 1.234,56."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    tekst = f"{value:,.2f}"
    return tekst.translate(str.maketrans({",": "X", ".": ","})).replace("X", ".")


templates.env.filters["dkk"] = _dkk


def _dato_dk(value) -> str:
    """Formatér en ISO-dato (YYYY-MM-DD) som '1. juni 2026'."""
    try:
        d = date.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return f"{d.day}. {_MAANEDER[d.month - 1]} {d.year}"


templates.env.filters["dato"] = _dato_dk


def _dage_til(value) -> Optional[int]:
    """Antal dage fra i dag til en given ISO-dato (negativt hvis datoen er passeret)."""
    try:
        d = date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    return (d - date.today()).days


templates.env.filters["dage_til"] = _dage_til

SECRET_KEY   = os.environ.get("SECRET_KEY", "skift-mig-i-railway-variables")
REGNSKAB_USER = os.environ.get("REGNSKAB_USERNAME", "admin")
REGNSKAB_PASS = os.environ.get("REGNSKAB_PASSWORD", "")

signer = URLSafeTimedSerializer(SECRET_KEY)
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 dage


# ── SESSION ──────────────────────────────────────────────────────────────

def get_session(request: Request):
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        return signer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def _kræv_login(request: Request) -> str:
    session = get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Ikke logget ind")
    return session["brugernavn"]


@app.get("/login", response_class=HTMLResponse)
async def login_side(request: Request):
    if get_session(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "fejl": None})


@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, brugernavn: str = Form(...), password: str = Form(...)):
    if brugernavn == REGNSKAB_USER and REGNSKAB_PASS and password == REGNSKAB_PASS:
        token = signer.dumps({"brugernavn": brugernavn})
        svar = RedirectResponse("/", status_code=303)
        svar.set_cookie("session", token, httponly=True, samesite="lax", max_age=SESSION_MAX_AGE)
        return svar
    return templates.TemplateResponse(
        "login.html", {"request": request, "fejl": "Forkert brugernavn eller adgangskode"}, status_code=401
    )


@app.get("/logout")
async def logout():
    svar = RedirectResponse("/login", status_code=302)
    svar.delete_cookie("session")
    return svar


# ── SIDER ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not get_session(request):
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/dashboard", status_code=302)


_DAGE = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
_MAANEDER = ["januar", "februar", "marts", "april", "maj", "juni", "juli",
             "august", "september", "oktober", "november", "december"]


def _dansk_dato(d: date) -> str:
    return f"{_DAGE[d.weekday()].capitalize()} {d.day}. {_MAANEDER[d.month - 1]} {d.year}"


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_side(request: Request):
    bruger = _kræv_login(request)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "bruger": bruger,
        "idag": _dansk_dato(date.today()),
        "kpi": database.hent_dashboard_kpi(),
        "posteringer": database.hent_posteringer(limit=6),
    })


@app.get("/bilag", response_class=HTMLResponse)
async def bilag_liste(request: Request):
    _kræv_login(request)
    return templates.TemplateResponse("bilag_liste.html", {
        "request": request,
        "bilag": database.hent_bilag(),
    })


@app.get("/bilag/upload", response_class=HTMLResponse)
async def bilag_upload_side(request: Request):
    _kræv_login(request)
    return templates.TemplateResponse("bilag_upload.html", {"request": request, "fejl": None})


def _behandl_bilag_upload(indhold: bytes, filename: str, content_type: str, bruger: str, kilde: str = "upload"):
    """Delt mellem manuel upload og mail-indbakke-upload: gem fil, AI-læs, opret bilag.
    Returnerer (bilag_id, felter, fejlbesked) — bilag_id er None ved dublet (fejlbesked sat)."""
    sha256 = hashlib.sha256(indhold).hexdigest()
    ext = os.path.splitext(filename or "")[1].lower() or ".bin"
    fil_sti = os.path.join(UPLOAD_DIR, f"{sha256}{ext}")
    if not os.path.exists(fil_sti):
        with open(fil_sti, "wb") as f:
            f.write(indhold)

    felter, ai_raw, ai_model = {}, None, None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            felter, ai_raw, ai_model = _læs_faktura_med_ai(indhold, content_type or "", api_key)
        except Exception as exc:
            felter = {}
            ai_raw = json.dumps({"fejl": str(exc)})

    felter = _foreslaa_kontonr_ved_behov(felter)

    bilag_id = database.opret_bilag(
        fil_sti=fil_sti, fil_sha256=sha256, ai_raw_json=ai_raw, ai_model=ai_model,
        felter={**felter, "bruger": bruger}, kilde=kilde,
    )
    if bilag_id is None:
        eksisterende = next((b for b in database.hent_bilag() if b["fil_sha256"] == sha256), None)
        return None, felter, f"Denne fil er allerede uploadet (bilag #{eksisterende['id'] if eksisterende else '?'})"
    return bilag_id, felter, None


@app.post("/bilag/upload")
async def bilag_upload_post(request: Request, fil: UploadFile = File(...), auto_bogfoer: str = Form("")):
    bruger = _kræv_login(request)
    indhold = await fil.read()
    bilag_id, felter, fejl = _behandl_bilag_upload(indhold, fil.filename, fil.content_type, bruger)
    if bilag_id is None:
        return templates.TemplateResponse("bilag_upload.html", {"request": request, "fejl": fejl})

    if auto_bogfoer == "on" and felter.get("beloeb_total") and felter.get("leverandoer_navn"):
        try:
            beriget = _berig_kreditor_felter_ved_behov(felter)
            database.godkend_og_bogfoer_bilag(bilag_id, bruger, {**beriget, "kontonr": beriget.get("kontonr", "4000")})
            return RedirectResponse("/bilag", status_code=303)
        except ValueError:
            pass  # AI-forslaget var ufuldstændigt — falder tilbage til manuel gennemgang
    return RedirectResponse(f"/bilag/{bilag_id}", status_code=303)


INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")


@app.post("/api/indbakke/upload")
async def api_indbakke_upload(request: Request, fil: UploadFile = File(...)):
    """Modtager PDF-fakturaer fra det eksterne mail-videresendelses-script (Google Apps
    Script). Autentificeres via en delt token, ikke login-session. Bogfører ALDRIG
    automatisk — lander altid som 'afventer' til manuel gennemgang."""
    token = request.headers.get("X-Ingest-Token", "")
    if not INGEST_TOKEN or not secrets.compare_digest(token, INGEST_TOKEN):
        raise HTTPException(401, "Ugyldig eller manglende token")
    indhold = await fil.read()
    bilag_id, felter, fejl = _behandl_bilag_upload(
        indhold, fil.filename, fil.content_type, bruger="mail-indbakke", kilde="mail",
    )
    if bilag_id is None:
        return JSONResponse({"ok": False, "fejl": fejl}, status_code=409)
    return {"ok": True, "bilag_id": bilag_id}


def _læs_faktura_med_ai(indhold: bytes, content_type: str, api_key: str):
    import anthropic as _ant
    client = _ant.Anthropic(api_key=api_key)
    b64 = base64.b64encode(indhold).decode()
    model = "claude-sonnet-4-6"

    er_pdf = "pdf" in content_type.lower()
    if er_pdf:
        dokument_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    else:
        media_type = content_type if content_type.startswith("image/") else "image/jpeg"
        dokument_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }

    prompt = (
        "Dette er en leverandørfaktura eller kvittering. "
        "Ekstrahér følgende felter og returnér KUN valid JSON (ingen forklarende tekst, ingen markdown):\n"
        "{\n"
        '  "leverandoer_navn": <firmanavn på leverandøren>,\n'
        '  "leverandoer_cvr": <CVR-nummer uden mellemrum, "" hvis ikke fundet>,\n'
        '  "fakturanr": <fakturanummer, "" hvis ikke fundet>,\n'
        '  "fakturadato": <dato i format YYYY-MM-DD, "" hvis ikke fundet>,\n'
        '  "forfaldsdato": <forfaldsdato i format YYYY-MM-DD, "" hvis ikke fundet>,\n'
        '  "beloeb_ex_moms": <beløb ekskl. moms som tal, 0 hvis ikke fundet>,\n'
        '  "moms_beloeb": <momsbeløb som tal, 0 hvis ikke moms er anført>,\n'
        '  "beloeb_total": <totalbeløb inkl. moms som tal>,\n'
        '  "linjer": [{"beskrivelse": <tekst>, "beloeb": <tal>}]\n'
        "}"
    )

    msg = client.messages.create(
        model=model, max_tokens=1024,
        messages=[{"role": "user", "content": [dokument_block, {"type": "text", "text": prompt}]}],
    )
    raw = msg.content[0].text.strip()
    raw_trimmed = raw.strip("`")
    if raw_trimmed.lower().startswith("json"):
        raw_trimmed = raw_trimmed[4:].strip()
    data = json.loads(raw_trimmed)
    for felt in ("leverandoer_navn", "leverandoer_cvr", "fakturanr", "fakturadato", "forfaldsdato"):
        data.setdefault(felt, "")
    for felt in ("beloeb_ex_moms", "moms_beloeb", "beloeb_total"):
        data.setdefault(felt, 0)
    return data, raw, model


@app.get("/bilag/{bilag_id}", response_class=HTMLResponse)
async def bilag_detalje(request: Request, bilag_id: int):
    _kræv_login(request)
    bilag = database.hent_et_bilag(bilag_id)
    if not bilag:
        raise HTTPException(404, "Bilag findes ikke")
    kreditor_post = database.hent_kreditor_post_for_bilag(bilag_id)
    bank_match = None
    if kreditor_post and kreditor_post["status"] == "udlignet":
        bank_match = database.hent_banktransaktion_for_match("kreditor", kreditor_post["id"])
    return templates.TemplateResponse("bilag_detalje.html", {
        "request": request, "bilag": bilag, "kontoplan": database.hent_kontoplan(), "fejl": None,
        "kreditor_post": kreditor_post, "bank_match": bank_match,
    })


@app.get("/bilag/{bilag_id}/fil")
async def bilag_fil(request: Request, bilag_id: int):
    _kræv_login(request)
    bilag = database.hent_et_bilag(bilag_id)
    if not bilag or not os.path.exists(bilag["fil_sti"]):
        raise HTTPException(404, "Bilagsfil findes ikke")
    return FileResponse(bilag["fil_sti"])


@app.post("/bilag/{bilag_id}/godkend")
async def bilag_godkend(
    request: Request, bilag_id: int,
    leverandoer_navn: str = Form(...), leverandoer_cvr: str = Form(""),
    fakturanr: str = Form(""), fakturadato: str = Form(""), forfaldsdato: str = Form(""),
    beloeb_ex_moms: float = Form(0), moms_beloeb: float = Form(0), beloeb_total: float = Form(0),
    kontonr: str = Form("4000"),
):
    bruger = _kræv_login(request)
    try:
        felter = _berig_kreditor_felter_ved_behov({
            "leverandoer_navn": leverandoer_navn, "leverandoer_cvr": leverandoer_cvr or None,
            "fakturanr": fakturanr, "fakturadato": fakturadato or date.today().isoformat(),
            "forfaldsdato": forfaldsdato or None,
            "beloeb_ex_moms": beloeb_ex_moms, "moms_beloeb": moms_beloeb, "beloeb_total": beloeb_total,
            "kontonr": kontonr,
        })
        database.godkend_og_bogfoer_bilag(bilag_id, bruger, felter)
    except ValueError as exc:
        bilag = database.hent_et_bilag(bilag_id)
        return templates.TemplateResponse("bilag_detalje.html", {
            "request": request, "bilag": bilag, "kontoplan": database.hent_kontoplan(), "fejl": str(exc),
        }, status_code=400)
    return RedirectResponse("/bilag", status_code=303)


@app.post("/bilag/{bilag_id}/afvis")
async def bilag_afvis(request: Request, bilag_id: int):
    bruger = _kræv_login(request)
    database.afvis_bilag(bilag_id, bruger)
    return RedirectResponse("/bilag", status_code=303)


@app.post("/bilag/{bilag_id}/slet")
async def bilag_slet(request: Request, bilag_id: int):
    bruger = _kræv_login(request)
    try:
        resultat = database.slet_bilag(bilag_id, bruger)
    except ValueError as exc:
        bilag = database.hent_et_bilag(bilag_id)
        if not bilag:
            raise HTTPException(404, "Bilag findes ikke")
        kreditor_post = database.hent_kreditor_post_for_bilag(bilag_id)
        bank_match = None
        if kreditor_post and kreditor_post["status"] == "udlignet":
            bank_match = database.hent_banktransaktion_for_match("kreditor", kreditor_post["id"])
        return templates.TemplateResponse("bilag_detalje.html", {
            "request": request, "bilag": bilag, "kontoplan": database.hent_kontoplan(), "fejl": str(exc),
            "kreditor_post": kreditor_post, "bank_match": bank_match,
        }, status_code=400)
    if resultat.get("fil_sti") and os.path.exists(resultat["fil_sti"]):
        os.remove(resultat["fil_sti"])
    return RedirectResponse("/bilag", status_code=303)


@app.get("/debitorer", response_class=HTMLResponse)
async def debitorer_side(request: Request):
    _kræv_login(request)
    return templates.TemplateResponse("debitor_liste.html", {
        "request": request,
        "debitorer": database.hent_debitorer(),
        "aabne_poster": database.hent_aabne_debitor_poster(),
    })


@app.get("/debitorer/ny", response_class=HTMLResponse)
async def debitor_ny_side(request: Request):
    _kræv_login(request)
    return templates.TemplateResponse("debitor_ny.html", {"request": request, "fejl": None, "debitor": None})


@app.post("/debitorer/ny")
async def debitor_ny_post(
    request: Request,
    navn: str = Form(...), cvr: str = Form(""), adresse: str = Form(""), email: str = Form(""),
    opret_faktura: str = Form(""),
    fakturanr: str = Form(""), dato_str: str = Form(""), forfaldsdato: str = Form(""),
    beloeb_ex_moms: float = Form(0), moms_beloeb: float = Form(0),
):
    bruger = _kræv_login(request)
    debitor_id = database.opret_debitor(navn, cvr or None, adresse or None, email or None)
    if opret_faktura == "on" and (beloeb_ex_moms or moms_beloeb):
        database.opret_debitor_faktura(
            debitor_id, bruger, fakturanr, dato_str or date.today().isoformat(),
            forfaldsdato or date.today().isoformat(), beloeb_ex_moms, moms_beloeb,
        )
    return RedirectResponse("/debitorer", status_code=303)


@app.get("/debitorer/{debitor_id}/rediger", response_class=HTMLResponse)
async def debitor_rediger_side(request: Request, debitor_id: int):
    _kræv_login(request)
    debitor = database.hent_en_debitor(debitor_id)
    if not debitor:
        raise HTTPException(404, "Debitor findes ikke")
    return templates.TemplateResponse("debitor_ny.html", {"request": request, "fejl": None, "debitor": debitor})


@app.post("/debitorer/{debitor_id}/rediger")
async def debitor_rediger_post(
    request: Request, debitor_id: int,
    navn: str = Form(...), cvr: str = Form(""), adresse: str = Form(""), email: str = Form(""),
):
    bruger = _kræv_login(request)
    database.opdater_debitor(debitor_id, bruger, navn, cvr or None, adresse or None, email or None)
    return RedirectResponse("/debitorer", status_code=303)


@app.get("/kreditorer", response_class=HTMLResponse)
async def kreditorer_side(request: Request):
    _kræv_login(request)
    return templates.TemplateResponse("kreditor_liste.html", {
        "request": request,
        "aabne_poster": database.hent_aabne_kreditor_poster(),
        "kreditorer": database.hent_kreditorer(),
    })


@app.get("/kreditorer/ny", response_class=HTMLResponse)
async def kreditor_ny_side(request: Request):
    _kræv_login(request)
    return templates.TemplateResponse("kreditor_ny.html", {
        "request": request, "fejl": None, "kreditor": None,
        "konteringsregler": [], "kontoplan": database.hent_kontoplan(),
    })


@app.post("/kreditorer/ny")
async def kreditor_ny_post(
    request: Request,
    navn: str = Form(...), cvr: str = Form(""), adresse: str = Form(""), email: str = Form(""),
    iban: str = Form(""), bic: str = Form(""), standard_kontonr: str = Form(""),
):
    _kræv_login(request)
    database.opret_kreditor(navn, cvr or None, adresse or None, email or None, iban or None, bic or None,
                             standard_kontonr or None)
    return RedirectResponse("/kreditorer", status_code=303)


@app.get("/kreditorer/{kreditor_id}/rediger", response_class=HTMLResponse)
async def kreditor_rediger_side(request: Request, kreditor_id: int):
    _kræv_login(request)
    kreditor = database.hent_en_kreditor(kreditor_id)
    if not kreditor:
        raise HTTPException(404, "Kreditor findes ikke")
    return templates.TemplateResponse("kreditor_ny.html", {
        "request": request, "fejl": None, "kreditor": kreditor,
        "konteringsregler": database.hent_konteringsregler(kreditor_id),
        "kontoplan": database.hent_kontoplan(),
    })


@app.post("/kreditorer/{kreditor_id}/rediger")
async def kreditor_rediger_post(
    request: Request, kreditor_id: int,
    navn: str = Form(...), cvr: str = Form(""), adresse: str = Form(""), email: str = Form(""),
    iban: str = Form(""), bic: str = Form(""), standard_kontonr: str = Form(""),
):
    bruger = _kræv_login(request)
    database.opdater_kreditor(kreditor_id, bruger, navn, cvr or None, adresse or None, email or None,
                               iban or None, bic or None, standard_kontonr or None)
    return RedirectResponse(f"/kreditorer/{kreditor_id}/rediger", status_code=303)


@app.post("/kreditorer/{kreditor_id}/konteringsregel")
async def konteringsregel_ny_post(
    request: Request, kreditor_id: int,
    match_tekst: str = Form(...), kontonr: str = Form(...), beskrivelse: str = Form(""),
):
    _kræv_login(request)
    database.opret_konteringsregel(kreditor_id, match_tekst.strip(), kontonr, beskrivelse.strip() or None)
    return RedirectResponse(f"/kreditorer/{kreditor_id}/rediger", status_code=303)


@app.post("/konteringsregler/{regel_id}/slet")
async def konteringsregel_slet_post(request: Request, regel_id: int, kreditor_id: int = Form(...)):
    bruger = _kræv_login(request)
    database.slet_konteringsregel(regel_id, bruger)
    return RedirectResponse(f"/kreditorer/{kreditor_id}/rediger", status_code=303)


CVR_API_USER_AGENT = "Nimbo Regnskab (regnskab-app; kontakt via Railway-projektejer)"


def _cvr_opslag(q: str) -> Optional[dict]:
    """Slår op i cvrapi.dk (cvr-nummer, p-nummer eller firmanavn). Returnerer None ved
    fejl/ingen match/kvote-overskredet — kaldere skal falde tilbage til andre kilder, ikke fejle."""
    q = (q or "").strip()
    if len(q) < 2:
        return None
    try:
        svar = requests.get(
            "https://cvrapi.dk/api",
            params={"search": q, "country": "dk"},
            headers={"User-Agent": CVR_API_USER_AGENT},
            timeout=8,
        )
    except requests.RequestException:
        return None
    if svar.status_code == 404:
        return None
    try:
        data = svar.json()
    except ValueError:
        return None
    # cvrapi.dk svarer med HTTP 200 selv ved kvote-overskridelse — fejlen ligger i "error"-feltet.
    if isinstance(data, dict) and data.get("error"):
        return None
    adresse_dele = [d for d in [data.get("address"), f"{data.get('zipcode', '')} {data.get('city', '')}".strip()] if d]
    navn = data.get("name") or ""
    if not navn:
        return None
    return {"navn": navn, "cvr": data.get("vat") or "", "adresse": ", ".join(adresse_dele)}


def _cvr_opslag_med_fejlbesked(q: str) -> tuple:
    """Som _cvr_opslag, men med en menneskelæselig fejlbesked til UI'en når intet findes."""
    q = (q or "").strip()
    if len(q) < 2:
        return None, "Skriv et CVR-nummer eller firmanavn"
    resultat = _cvr_opslag(q)
    if resultat:
        return resultat, None
    return None, "Ingen match fundet (eller dagligt opslagsloft hos CVR-registeret er nået — udfyld evt. manuelt)"


@app.get("/api/cvr-opslag")
async def api_cvr_opslag(request: Request, q: str = ""):
    _kræv_login(request)
    resultat, fejl = _cvr_opslag_med_fejlbesked(q)
    if not resultat:
        return JSONResponse({"ok": False, "fejl": fejl}, status_code=404)
    return {"ok": True, **resultat}


def _berig_kreditor_felter_ved_behov(felter: dict) -> dict:
    """Del af upload-flowet: hvis bilagets CVR ikke matcher en eksisterende kreditor,
    slås CVR'et op i registeret så den nye kreditor oprettes med det officielle navn/adresse
    i stedet for AI'ens rå OCR-tekst. Matcher CVR'et allerede en kreditor, spares opslaget."""
    cvr_raw = (felter.get("leverandoer_cvr") or "").strip()
    cvr_cifre = "".join(ch for ch in cvr_raw if ch.isdigit())
    if len(cvr_cifre) != 8:
        return felter
    if database.find_kreditor_by_cvr(cvr_cifre):
        return felter  # matcher eksisterende kreditor — intet opslag nødvendigt
    opslag = _cvr_opslag(cvr_cifre)
    if not opslag:
        return felter  # opslag fejlede/ingen match — behold AI-forslaget som det er
    return {
        **felter,
        "leverandoer_navn": opslag["navn"],
        "leverandoer_cvr": cvr_cifre,
        "leverandoer_adresse": opslag["adresse"],
    }


def _foreslaa_kontonr_ved_behov(felter: dict) -> dict:
    """Del af upload-flowet: hvis fakturaens CVR matcher en kendt kreditor, foreslå en
    kontonr ud fra (i prioriteret rækkefølge) leverandørens konteringsregler, dennes faste
    standardkonto, eller den konto der historisk er brugt oftest — så gennemgangssiden
    allerede har det rigtige valg forudfyldt i stedet for altid at falde tilbage til 4000."""
    cvr_cifre = "".join(ch for ch in (felter.get("leverandoer_cvr") or "") if ch.isdigit())
    if len(cvr_cifre) != 8:
        return felter
    kreditor = database.find_kreditor_by_cvr(cvr_cifre)
    if not kreditor:
        return felter
    linjer_tekst = " ".join(
        (l.get("beskrivelse") or "") for l in (felter.get("linjer") or []) if isinstance(l, dict)
    )
    soegetekst = " ".join(filter(None, [felter.get("fakturanr", ""), linjer_tekst]))
    kontonr = database.foreslaa_kontering(kreditor["id"], soegetekst)
    if kontonr:
        return {**felter, "kontonr": kontonr}
    return felter


MAANED_NAVNE = ["Jan", "Feb", "Mar", "Apr", "Maj", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]


@app.get("/posteringer", response_class=HTMLResponse)
async def posteringer_side(request: Request, aar: Optional[int] = None, maaned: Optional[int] = None):
    _kræv_login(request)
    i_dag = date.today()
    aar = aar or i_dag.year
    if maaned is None:
        maaned = i_dag.month
    return templates.TemplateResponse("posteringer.html", {
        "request": request,
        "posteringer": database.hent_posteringer(limit=1000, aar=aar, maaned=maaned or None),
        "aar": aar,
        "maaned": maaned,
        "maaned_navne": MAANED_NAVNE,
    })


# ── Kontoplan ────────────────────────────────────────────────────────────

KONTOTYPE_LABELS = {
    "omsaetning": "Omsætning", "omkostning": "Omkostning", "aktiv": "Aktiv",
    "passiv": "Passiv", "moms": "Moms", "status": "Status/egenkapital",
}
MOMS_KODE_LABELS = {
    "salgsmoms": "Salgsmoms (25%)",
    "koebsmoms": "Købsmoms (25%)",
    "momsfri": "Momsfri leverance",
    "reduceret": "Delvis fradrag (fx repræsentation)",
    "eu_vare": "EU-varehandel (omvendt betalingspligt)",
    "eu_ydelse": "EU-ydelseskøb (omvendt betalingspligt)",
}
templates.env.globals["kontotype_label"] = lambda kt: KONTOTYPE_LABELS.get(kt, kt)
templates.env.globals["moms_kode_label"] = lambda mk: MOMS_KODE_LABELS.get(mk, "Ingen") if mk else "Ingen"
templates.env.globals["kontotype_valg"] = list(KONTOTYPE_LABELS.items())
templates.env.globals["moms_kode_valg"] = list(MOMS_KODE_LABELS.items())


@app.get("/kontoplan", response_class=HTMLResponse)
async def kontoplan_side(request: Request):
    _kræv_login(request)
    return templates.TemplateResponse("kontoplan.html", {
        "request": request, "konti": database.hent_kontoplan(kun_aktive=False),
    })


@app.get("/kontoplan/ny", response_class=HTMLResponse)
async def konto_ny_side(request: Request):
    _kræv_login(request)
    return templates.TemplateResponse("konto_form.html", {"request": request, "fejl": None, "konto": None})


@app.post("/kontoplan/ny")
async def konto_ny_post(
    request: Request,
    kontonr: str = Form(...), navn: str = Form(...), kontotype: str = Form(...), moms_kode: str = Form(""),
):
    bruger = _kræv_login(request)
    try:
        database.opret_konto(kontonr.strip(), navn.strip(), kontotype, moms_kode or None, bruger)
    except ValueError as exc:
        return templates.TemplateResponse("konto_form.html", {
            "request": request, "fejl": str(exc),
            "konto": {"kontonr": kontonr, "navn": navn, "kontotype": kontotype, "moms_kode": moms_kode, "aktiv": 1},
        }, status_code=400)
    return RedirectResponse("/kontoplan", status_code=303)


@app.get("/kontoplan/{kontonr}/rediger", response_class=HTMLResponse)
async def konto_rediger_side(request: Request, kontonr: str):
    _kræv_login(request)
    konto = database.hent_en_konto(kontonr)
    if not konto:
        raise HTTPException(404, "Konto findes ikke")
    return templates.TemplateResponse("konto_form.html", {"request": request, "fejl": None, "konto": konto})


@app.post("/kontoplan/{kontonr}/rediger")
async def konto_rediger_post(
    request: Request, kontonr: str,
    navn: str = Form(...), kontotype: str = Form(...), moms_kode: str = Form(""), aktiv: str = Form(""),
):
    bruger = _kræv_login(request)
    database.opdater_konto(kontonr, navn.strip(), kontotype, moms_kode or None, aktiv == "on", bruger)
    return RedirectResponse("/kontoplan", status_code=303)


# ── Rapporter ────────────────────────────────────────────────────────────

@app.get("/rapporter/resultatopgoerelse", response_class=HTMLResponse)
async def resultatopgoerelse_side(request: Request, fra: str = "", til: str = ""):
    _kræv_login(request)
    i_dag = date.today()
    fra = fra or date(i_dag.year, 1, 1).isoformat()
    til = til or i_dag.isoformat()
    return templates.TemplateResponse("resultatopgoerelse.html", {
        "request": request, "rapport": database.hent_resultatopgoerelse(fra, til),
    })


@app.get("/rapporter/balance", response_class=HTMLResponse)
async def balance_side(request: Request, til: str = ""):
    _kræv_login(request)
    til = til or date.today().isoformat()
    return templates.TemplateResponse("balance.html", {
        "request": request, "rapport": database.hent_balance(til),
    })


@app.get("/rapporter/moms", response_class=HTMLResponse)
async def moms_side(request: Request, aar: Optional[int] = None, kvartal: Optional[int] = None):
    _kræv_login(request)
    i_dag = date.today()
    aar = aar or i_dag.year
    kvartal = kvartal or ((i_dag.month - 1) // 3 + 1)
    return templates.TemplateResponse("moms.html", {
        "request": request, "rapport": database.hent_momsopgoerelse(aar, kvartal),
    })


# ── Bank (simuleret indlæsning + match) ─────────────────────────────────

@app.get("/bank", response_class=HTMLResponse)
async def bank_side(request: Request):
    _kræv_login(request)
    transaktioner = database.hent_banktransaktioner()
    for tx in transaktioner:
        tx["forslag"] = database.foreslaa_matches(tx) if tx["match_status"] == "uafklaret" else []
    return templates.TemplateResponse("bank_liste.html", {
        "request": request,
        "transaktioner": transaktioner,
        "forbindelser": database.hent_bank_forbindelser(),
        "bank_api_konfigureret": enable_banking_klient.konfigureret(),
    })


@app.get("/bank/indlaes", response_class=HTMLResponse)
async def bank_indlaes_side(request: Request):
    _kræv_login(request)
    return templates.TemplateResponse("bank_indlaes.html", {"request": request, "fejl": None})


@app.post("/bank/indlaes")
async def bank_indlaes_post(request: Request, linjer: str = Form(...)):
    _kræv_login(request)
    parsed = []
    fejl_linjer = []
    for i, raw in enumerate(linjer.splitlines(), start=1):
        raw = raw.strip()
        if not raw:
            continue
        dele = [d.strip() for d in raw.split(";")]
        if len(dele) < 2:
            fejl_linjer.append(f"linje {i}: for få felter (forventet dato;beløb;tekst)")
            continue
        dato_str, beloeb_str = dele[0], dele[1]
        tekst = dele[2] if len(dele) > 2 else ""
        try:
            beloeb = float(beloeb_str.replace(",", "."))
        except ValueError:
            fejl_linjer.append(f"linje {i}: ugyldigt beløb '{beloeb_str}'")
            continue
        try:
            date.fromisoformat(dato_str)
        except ValueError:
            fejl_linjer.append(f"linje {i}: ugyldig dato '{dato_str}' (brug YYYY-MM-DD)")
            continue
        parsed.append({"dato": dato_str, "beloeb": beloeb, "tekst": tekst})

    if fejl_linjer:
        return templates.TemplateResponse("bank_indlaes.html", {
            "request": request, "fejl": "Kunne ikke indlæse: " + "; ".join(fejl_linjer),
        }, status_code=400)

    database.indlæs_banktransaktioner(parsed)
    return RedirectResponse("/bank", status_code=303)


@app.post("/bank/{transaktion_id}/match")
async def bank_match_post(request: Request, transaktion_id: int,
                           modpart_type: str = Form(...), post_id: int = Form(...)):
    bruger = _kræv_login(request)
    try:
        database.godkend_bank_match(transaktion_id, modpart_type, post_id, bruger)
    except ValueError:
        pass  # forslaget var forældet (posten blev fx annulleret i mellemtiden) — siden genindlæses uden
    return RedirectResponse("/bank", status_code=303)


@app.post("/bank/{transaktion_id}/ignorer")
async def bank_ignorer_post(request: Request, transaktion_id: int):
    bruger = _kræv_login(request)
    database.ignorer_banktransaktion(transaktion_id, bruger)
    return RedirectResponse("/bank", status_code=303)


# ── Bankforbindelse (Enable Banking) ─────────────────────────────────────

@app.get("/bank/forbind", response_class=HTMLResponse)
async def bank_forbind_side(request: Request):
    _kræv_login(request)
    if not enable_banking_klient.konfigureret():
        return templates.TemplateResponse("bank_forbind.html", {
            "request": request, "institutioner": [], "fejl": None,
            "ikke_konfigureret": True,
        })
    try:
        institutioner = enable_banking_klient.hent_banker()
    except requests.RequestException as exc:
        return templates.TemplateResponse("bank_forbind.html", {
            "request": request, "institutioner": [], "ikke_konfigureret": False,
            "fejl": f"Kunne ikke hente liste over banker: {exc}",
        })
    return templates.TemplateResponse("bank_forbind.html", {
        "request": request, "institutioner": institutioner, "fejl": None, "ikke_konfigureret": False,
    })


@app.post("/bank/forbind")
async def bank_forbind_post(request: Request, aspsp_navn: str = Form(...)):
    _kræv_login(request)
    state = secrets.token_urlsafe(16)
    redirect_url = str(request.base_url).rstrip("/") + "/bank/forbind/callback"
    try:
        session = enable_banking_klient.start_session(aspsp_navn, "DK", redirect_url, state)
    except requests.RequestException as exc:
        institutioner = enable_banking_klient.hent_banker() if enable_banking_klient.konfigureret() else []
        return templates.TemplateResponse("bank_forbind.html", {
            "request": request, "institutioner": institutioner, "ikke_konfigureret": False,
            "fejl": f"Kunne ikke starte godkendelsen hos banken: {exc}",
        }, status_code=502)
    return RedirectResponse(session["url"], status_code=303)


@app.get("/bank/forbind/callback", response_class=HTMLResponse)
async def bank_forbind_callback(request: Request, code: str = "", state: str = "",
                                 error: str = "", error_description: str = ""):
    _kræv_login(request)
    if error:
        return templates.TemplateResponse("bank_forbind.html", {
            "request": request, "institutioner": [], "ikke_konfigureret": False,
            "fejl": f"Banken afviste godkendelsen: {error_description or error}",
        }, status_code=400)
    try:
        session = enable_banking_klient.afslut_session(code)
        institution_navn = (session.get("aspsp") or {}).get("name") or "Ukendt bank"
        consent_expires_ts = (date.today() + timedelta(days=90)).isoformat()
        for konto in session.get("accounts", []):
            account_uid = konto.get("uid")
            if not account_uid:
                continue
            iban = (konto.get("account_id") or {}).get("iban")
            forbindelse_id = database.opret_bank_forbindelse(
                institution_navn, institution_navn, account_uid, iban,
                session.get("session_id"), consent_expires_ts,
            )
            try:
                database.opdater_bank_saldo(forbindelse_id, enable_banking_klient.hent_saldo(account_uid))
            except requests.RequestException:
                logger.exception("Kunne ikke hente indledende saldo for bankforbindelse %s", forbindelse_id)
    except requests.RequestException as exc:
        return templates.TemplateResponse("bank_forbind.html", {
            "request": request, "institutioner": [], "ikke_konfigureret": False,
            "fejl": f"Bankforbindelsen kunne ikke fuldføres: {exc}",
        }, status_code=502)
    return RedirectResponse("/bank", status_code=303)


@app.post("/bank/synkroniser")
async def bank_synkroniser_post(request: Request):
    _kræv_login(request)
    _synkroniser_bankforbindelser()
    return RedirectResponse("/bank", status_code=303)


@app.post("/bank/forbindelse/{forbindelse_id}/luk")
async def bank_forbindelse_luk_post(request: Request, forbindelse_id: int):
    bruger = _kræv_login(request)
    database.luk_bank_forbindelse(forbindelse_id, bruger)
    return RedirectResponse("/bank", status_code=303)


def _synkroniser_bankforbindelser() -> int:
    """Henter nye transaktioner + saldo for alle aktive bankforbindelser. Fejl på én forbindelse
    stopper ikke synkronisering af de øvrige (men logges, så det ikke fejler i stilhed).
    Returnerer samlet antal nye transaktioner."""
    if not enable_banking_klient.konfigureret():
        return 0
    antal_nye = 0
    for forbindelse in database.hent_bank_forbindelser():
        if forbindelse["status"] != "aktiv" or not forbindelse["account_id"]:
            continue
        try:
            transaktioner = enable_banking_klient.hent_transaktioner(forbindelse["account_id"])
            antal_nye += database.indlæs_eksterne_banktransaktioner(forbindelse["id"], transaktioner)
        except requests.RequestException:
            logger.exception("Kunne ikke hente transaktioner for bankforbindelse %s", forbindelse["id"])
        try:
            saldo = enable_banking_klient.hent_saldo(forbindelse["account_id"])
            database.opdater_bank_saldo(forbindelse["id"], saldo)
        except requests.RequestException:
            logger.exception("Kunne ikke hente saldo for bankforbindelse %s", forbindelse["id"])
    return antal_nye
