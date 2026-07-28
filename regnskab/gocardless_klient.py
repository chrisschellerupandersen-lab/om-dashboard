"""Tynd klient mod GoCardless Bank Account Data (tidligere Nordigen) — PSD2-baseret,
read-only bankdata. Kræver GOCARDLESS_SECRET_ID + GOCARDLESS_SECRET_KEY (gratis konto
på https://bankaccountdata.gocardless.com/)."""

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

import database

BASE_URL = "https://bankaccountdata.gocardless.com/api/v2"


def konfigureret() -> bool:
    return bool(os.environ.get("GOCARDLESS_SECRET_ID") and os.environ.get("GOCARDLESS_SECRET_KEY"))


def _hent_gyldig_token() -> str:
    """Genbruger det cachede access-token hvis stadig gyldigt, fornyer via refresh-token
    hvis det er udløbet, eller henter et helt nyt token-par som sidste udvej."""
    if not konfigureret():
        raise RuntimeError("GOCARDLESS_SECRET_ID/GOCARDLESS_SECRET_KEY er ikke sat")

    tok = database.hent_gocardless_token()
    nu = datetime.utcnow()

    if tok and tok.get("access_token") and tok.get("access_expires_ts"):
        try:
            if datetime.fromisoformat(tok["access_expires_ts"]) > nu + timedelta(minutes=2):
                return tok["access_token"]
        except ValueError:
            pass

    if tok and tok.get("refresh_token") and tok.get("refresh_expires_ts"):
        try:
            if datetime.fromisoformat(tok["refresh_expires_ts"]) > nu + timedelta(minutes=2):
                svar = requests.post(f"{BASE_URL}/token/refresh/", json={"refresh": tok["refresh_token"]}, timeout=15)
                if svar.status_code == 200:
                    data = svar.json()
                    access_expires_ts = (nu + timedelta(seconds=data["access_expires"])).isoformat()
                    database.gem_gocardless_token(
                        data["access"], access_expires_ts, tok["refresh_token"], tok["refresh_expires_ts"],
                    )
                    return data["access"]
        except (ValueError, KeyError, requests.RequestException):
            pass  # falder tilbage til helt nyt token-par

    svar = requests.post(f"{BASE_URL}/token/new/", json={
        "secret_id": os.environ["GOCARDLESS_SECRET_ID"],
        "secret_key": os.environ["GOCARDLESS_SECRET_KEY"],
    }, timeout=15)
    svar.raise_for_status()
    data = svar.json()
    access_expires_ts = (nu + timedelta(seconds=data["access_expires"])).isoformat()
    refresh_expires_ts = (nu + timedelta(seconds=data["refresh_expires"])).isoformat()
    database.gem_gocardless_token(data["access"], access_expires_ts, data["refresh"], refresh_expires_ts)
    return data["access"]


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_hent_gyldig_token()}", "Accept": "application/json"}


def hent_institutioner(country: str = "dk") -> List[Dict[str, Any]]:
    svar = requests.get(f"{BASE_URL}/institutions/", params={"country": country}, headers=_headers(), timeout=15)
    svar.raise_for_status()
    return svar.json()


def opret_aftale(institution_id: str, max_historical_days: int = 90) -> str:
    svar = requests.post(f"{BASE_URL}/agreements/enduser/", headers=_headers(), timeout=15, json={
        "institution_id": institution_id,
        "max_historical_days": max_historical_days,
        "access_valid_for_days": 90,
        "access_scope": ["balances", "details", "transactions"],
    })
    svar.raise_for_status()
    return svar.json()["id"]


def opret_requisition(institution_id: str, agreement_id: str, redirect_url: str, reference: str) -> Dict[str, Any]:
    """Returnerer bl.a. {"id": requisition_id, "link": <URL brugeren skal godkende på>}."""
    svar = requests.post(f"{BASE_URL}/requisitions/", headers=_headers(), timeout=15, json={
        "redirect": redirect_url,
        "institution_id": institution_id,
        "reference": reference,
        "agreement": agreement_id,
        "user_language": "DA",
    })
    svar.raise_for_status()
    return svar.json()


def hent_requisition(requisition_id: str) -> Dict[str, Any]:
    svar = requests.get(f"{BASE_URL}/requisitions/{requisition_id}/", headers=_headers(), timeout=15)
    svar.raise_for_status()
    return svar.json()


def hent_alle_requisitioner() -> List[Dict[str, Any]]:
    svar = requests.get(f"{BASE_URL}/requisitions/", headers=_headers(), timeout=15, params={"limit": 100})
    svar.raise_for_status()
    return svar.json().get("results", [])


def hent_konto_iban(account_id: str) -> Optional[str]:
    try:
        svar = requests.get(f"{BASE_URL}/accounts/{account_id}/details/", headers=_headers(), timeout=15)
        svar.raise_for_status()
        return svar.json().get("account", {}).get("iban")
    except requests.RequestException:
        return None


def hent_transaktioner(account_id: str) -> List[Dict[str, Any]]:
    """Returnerer en normaliseret liste [{"ekstern_id","dato","beloeb","tekst"}] af BOGFØRTE
    (booked) transaktioner — pending-transaktioner har intet stabilt id endnu og springes over,
    de dukker op igen som booked ved næste synkronisering."""
    svar = requests.get(f"{BASE_URL}/accounts/{account_id}/transactions/", headers=_headers(), timeout=30)
    svar.raise_for_status()
    booked = svar.json().get("transactions", {}).get("booked", [])
    out = []
    for t in booked:
        ekstern_id = t.get("transactionId") or t.get("internalTransactionId")
        beloeb_raw = (t.get("transactionAmount") or {}).get("amount")
        if not ekstern_id or beloeb_raw is None:
            continue
        tekst = (
            t.get("remittanceInformationUnstructured")
            or " ".join(t.get("remittanceInformationUnstructuredArray") or [])
            or t.get("creditorName") or t.get("debtorName") or ""
        )
        out.append({
            "ekstern_id": ekstern_id,
            "dato": t.get("bookingDate") or t.get("valueDate"),
            "beloeb": float(beloeb_raw),
            "tekst": tekst,
        })
    return out
