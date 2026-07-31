"""Klient mod Enable Banking (enablebanking.com) — PSD2-baseret, read-only bankdata
for danske banker via MitID. Erstatter GoCardless Bank Account Data, som lukkede for
nye tilmeldinger i juli 2025.

Kræver ENABLEBANKING_APP_ID + ENABLEBANKING_PRIVATE_KEY (et RSA-nøglepar/certifikat
registreret som "application" på enablebanking.com). Hver anmodning autentificeres
med en kortlivet, selvsigneret JWT — der er intet server-cachet token som hos
GoCardless."""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import jwt
import requests

BASE_URL = "https://api.enablebanking.com"


def konfigureret() -> bool:
    return bool(os.environ.get("ENABLEBANKING_APP_ID") and os.environ.get("ENABLEBANKING_PRIVATE_KEY"))


def _lav_jwt() -> str:
    if not konfigureret():
        raise RuntimeError("ENABLEBANKING_APP_ID/ENABLEBANKING_PRIVATE_KEY er ikke sat")
    nu = int(time.time())
    payload = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": nu, "exp": nu + 3600}
    headers = {"kid": os.environ["ENABLEBANKING_APP_ID"]}
    return jwt.encode(payload, os.environ["ENABLEBANKING_PRIVATE_KEY"], algorithm="RS256", headers=headers)


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_lav_jwt()}", "Accept": "application/json"}


def hent_banker(country: str = "DK", psu_type: str = "business") -> List[Dict[str, Any]]:
    svar = requests.get(
        f"{BASE_URL}/aspsps", params={"country": country, "psu_type": psu_type}, headers=_headers(), timeout=15,
    )
    svar.raise_for_status()
    data = svar.json()
    return data.get("aspsps", []) if isinstance(data, dict) else data


def start_session(aspsp_navn: str, country: str, redirect_url: str, state: str,
                   psu_type: str = "business") -> Dict[str, Any]:
    """Returnerer bl.a. {"url": <URL brugeren skal godkende på>, "authorization_id": ...}."""
    gyldig_til = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    svar = requests.post(f"{BASE_URL}/auth", headers=_headers(), timeout=15, json={
        "aspsp": {"name": aspsp_navn, "country": country},
        "access": {"valid_until": gyldig_til},
        "state": state,
        "redirect_url": redirect_url,
        "psu_type": psu_type,
    })
    svar.raise_for_status()
    return svar.json()


def afslut_session(code: str) -> Dict[str, Any]:
    """Udveksler engangskoden fra redirect-callbacket til en session med kontoliste.
    Returnerer {"session_id": ..., "accounts": [{"uid", "account_id": {"iban"}, "name", ...}], "aspsp": {...}}."""
    svar = requests.post(f"{BASE_URL}/sessions", headers=_headers(), timeout=15, json={"code": code})
    svar.raise_for_status()
    return svar.json()


def hent_saldo(account_uid: str) -> Optional[float]:
    """Returnerer kontoens aktuelle saldo (foretrækker 'closingBooked'/'expected', ellers første
    tilgængelige), eller None hvis banken ikke leverer nogen saldo for kontoen."""
    svar = requests.get(f"{BASE_URL}/accounts/{account_uid}/balances", headers=_headers(), timeout=15)
    svar.raise_for_status()
    balances = svar.json().get("balances", [])
    if not balances:
        return None
    prioritet = ["closingBooked", "expected", "interimAvailable", "openingBooked"]
    balances_by_type = {b.get("balance_type"): b for b in balances}
    for btype in prioritet:
        if btype in balances_by_type:
            beloeb = (balances_by_type[btype].get("balance_amount") or {}).get("amount")
            if beloeb is not None:
                return float(beloeb)
    beloeb = (balances[0].get("balance_amount") or {}).get("amount")
    return float(beloeb) if beloeb is not None else None


def hent_transaktioner(account_uid: str, dage_tilbage: int = 90) -> List[Dict[str, Any]]:
    """Returnerer en normaliseret liste [{"ekstern_id","dato","beloeb","tekst"}]. Beløbets
    fortegn afledes af credit_debit_indicator (DBIT=negativ/udbetaling, CRDT=positiv/indbetaling),
    da Enable Banking selv altid returnerer et positivt tal."""
    dato_fra = (datetime.now(timezone.utc) - timedelta(days=dage_tilbage)).date().isoformat()
    ud: List[Dict[str, Any]] = []
    continuation_key: Optional[str] = None

    for _ in range(20):  # sikkerhedsgrænse mod uendelig pagineringsløkke
        params: Dict[str, str] = {"date_from": dato_fra}
        if continuation_key:
            params["continuation_key"] = continuation_key
        svar = requests.get(
            f"{BASE_URL}/accounts/{account_uid}/transactions", params=params, headers=_headers(), timeout=30,
        )
        svar.raise_for_status()
        data = svar.json()

        for t in data.get("transactions", []):
            ekstern_id = t.get("transaction_id") or t.get("entry_reference")
            beloeb_raw = (t.get("transaction_amount") or {}).get("amount")
            if not ekstern_id or beloeb_raw is None:
                continue
            beloeb = abs(float(beloeb_raw))
            if t.get("credit_debit_indicator") == "DBIT":
                beloeb = -beloeb

            tekst_dele = t.get("remittance_information") or []
            tekst = " ".join(tekst_dele) if isinstance(tekst_dele, list) else str(tekst_dele)
            if not tekst:
                tekst = (t.get("creditor") or {}).get("name") or (t.get("debtor") or {}).get("name") or ""

            ud.append({
                "ekstern_id": ekstern_id,
                "dato": t.get("booking_date") or t.get("transaction_date"),
                "beloeb": beloeb,
                "tekst": tekst,
            })

        continuation_key = data.get("continuation_key")
        if not continuation_key:
            break

    return ud
