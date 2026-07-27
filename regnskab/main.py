import os
import json
import base64
import hashlib
from datetime import date
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import database

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    database.init_db()
    yield


app = FastAPI(title="Regnskab", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

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
    return RedirectResponse("/bilag", status_code=302)


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


@app.post("/bilag/upload")
async def bilag_upload_post(request: Request, fil: UploadFile = File(...)):
    bruger = _kræv_login(request)
    indhold = await fil.read()
    sha256 = hashlib.sha256(indhold).hexdigest()

    ext = os.path.splitext(fil.filename or "")[1].lower() or ".bin"
    fil_sti = os.path.join(UPLOAD_DIR, f"{sha256}{ext}")
    if not os.path.exists(fil_sti):
        with open(fil_sti, "wb") as f:
            f.write(indhold)

    felter, ai_raw, ai_model = {}, None, None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            felter, ai_raw, ai_model = _læs_faktura_med_ai(indhold, fil.content_type or "", api_key)
        except Exception as exc:
            felter = {}
            ai_raw = json.dumps({"fejl": str(exc)})

    bilag_id = database.opret_bilag(
        fil_sti=fil_sti, fil_sha256=sha256, ai_raw_json=ai_raw, ai_model=ai_model,
        felter={**felter, "bruger": bruger},
    )
    if bilag_id is None:
        eksisterende = next((b for b in database.hent_bilag() if b["fil_sha256"] == sha256), None)
        return templates.TemplateResponse("bilag_upload.html", {
            "request": request,
            "fejl": f"Denne fil er allerede uploadet (bilag #{eksisterende['id'] if eksisterende else '?'})",
        })
    return RedirectResponse(f"/bilag/{bilag_id}", status_code=303)


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
    return templates.TemplateResponse("bilag_detalje.html", {
        "request": request, "bilag": bilag, "kontoplan": database.hent_kontoplan(), "fejl": None,
    })


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
        database.godkend_og_bogfoer_bilag(bilag_id, bruger, {
            "leverandoer_navn": leverandoer_navn, "leverandoer_cvr": leverandoer_cvr or None,
            "fakturanr": fakturanr, "fakturadato": fakturadato or date.today().isoformat(),
            "forfaldsdato": forfaldsdato or None,
            "beloeb_ex_moms": beloeb_ex_moms, "moms_beloeb": moms_beloeb, "beloeb_total": beloeb_total,
            "kontonr": kontonr,
        })
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
    return templates.TemplateResponse("debitor_ny.html", {"request": request, "fejl": None})


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


@app.get("/kreditorer", response_class=HTMLResponse)
async def kreditorer_side(request: Request):
    _kræv_login(request)
    return templates.TemplateResponse("kreditor_liste.html", {
        "request": request,
        "aabne_poster": database.hent_aabne_kreditor_poster(),
    })


@app.get("/posteringer", response_class=HTMLResponse)
async def posteringer_side(request: Request):
    _kræv_login(request)
    return templates.TemplateResponse("posteringer.html", {
        "request": request,
        "posteringer": database.hent_posteringer(),
    })
