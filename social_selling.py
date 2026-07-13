"""
Social selling-motor til Organic Market Greve.

Genererer automatisk færdige, data-drevne Facebook-opslag ud fra ugedag,
forretningens profil og (når tilgængeligt) rigtige tal fra dashboardet.
Kan valgfrit forbedres af Claude når ANTHROPIC_API_KEY er sat, og publiceres
direkte på en Facebook-side når FB_PAGE_ID + FB_PAGE_TOKEN er sat.

Ingen ToS-risiko: kun opslag på egen side. Ingen automatiske cold-DM'er.
"""
from __future__ import annotations

import os
import time
import hashlib
from datetime import date, datetime
from typing import Dict, List, Optional

# Kunde-vendte landingssider (aldrig det interne dashboard).
# B2B  = forudbestilling til virksomheder (/b2b)
# FEST = private lejligheder: fødselsdag/konfirmation/fest (/fest)
B2B_LINK  = os.environ.get(
    "SOCIAL_B2B_LINK",
    "https://bestilling-app-production.up.railway.app/b2b",
).strip()
FEST_LINK = os.environ.get(
    "SOCIAL_FEST_LINK",
    "https://bestilling-app-production.up.railway.app/fest",
).strip()
ADRESSE = "Greve Strandvej 20"

# Opslagstyper der er virksomheds-rettede → link til B2B-landingssiden.
_B2B_TYPER = {
    "b2b-moede", "firma-fast", "saeson-opstart", "saeson-julefrokost-booking",
}


def _link_for(type_: str) -> str:
    """Vælg kunde-landingsside efter opslagstype. Tom = ingen link (kun CTA)."""
    return B2B_LINK if type_ in _B2B_TYPER else FEST_LINK

# ── Opslags-skabeloner pr. ugedag (0=mandag … 6=søndag) ───────────────────────
# STRATEGI: driv bestillinger til lejligheder (fødselsdage, fester, konfirmation,
# reception m.m.) og til virksomheder (mødeforplejning, firmafester, julefrokost).
# Hver ugedag har flere varianter; motoren vælger deterministisk ud fra datoen.
# CTA'er er samtale-baserede ("skriv til os") — passer til bespoke tilbud og
# fødes senere direkte ind i Messenger-autosvar (fase 2).

# VIGTIGT: Nævn KUN varer vi faktisk har i bestillings-appen.
# Brød: surdejsbrød, signaturbrød, flute, rugbrød · Boller: surdejsboller, teboller
# Wienerbrød: croissant, kanelsnegl, kanelsnurrer, kardemommesnurrer, tebirkes,
#   frøsnapper, wienerstang, kanelstang · Kager (stykker): træstammer, hindbærsnitter,
#   napoleonshat, cookies, kokostoppe, romkugler, studenterbrød (halv plade)
# Til bordet: juice, smør, syltetøj, pålægschokolade · Små-catering: tapaskasse,
#   burgerpose (begge 2 pers). INGEN lagkager/kagemand/kransekage — dem har vi ikke.
_SKABELONER: Dict[int, List[Dict]] = {
    0: [  # Mandag — virksomheder / B2B
        {"type": "b2b-moede",
         "tekst": ("Skal I holde møde eller event i denne uge? 🤝 Vi pakker "
                   "friskbagt surdejsbrød, boller, wienerbrød og kager klar — plus "
                   "juice til bordet. Klar til afhentning fra kl. 06.\n\n"
                   "Virksomheder i Greve: skriv 'FIRMA' + antal personer."),
         "cta": "Skriv 'FIRMA' + antal personer", "billede_hint": "Mødebakke med surdejsbrød, boller og wienerbrød"},
        {"type": "firma-fast",
         "tekst": ("Faste morgenbrød-aftaler til kontoret 🥐 Friskbagt surdejsbrød, "
                   "boller og wienerbrød, bestilt i forvejen og klar til afhentning "
                   "fra kl. 06 — uden at nogen hos jer skal tænke på det.\n\n"
                   "Skriv 'FIRMA', så laver vi en aftale."),
         "cta": "Skriv 'FIRMA'", "billede_hint": "Kurv med friskbagt brød og wienerbrød til kontor"},
    ],
    1: [  # Tirsdag — kvalitet knyttet til lejligheder
        {"type": "kage-haandvaerk",
         "tekst": ("Skal I fejre noget? 🎉 Forudbestil et kaffebord af friskbagt "
                   "wienerbrød — croissant, kanelsnegl, kardemommesnurrer — og kager "
                   "som træstammer, hindbærsnitter og napoleonshat. Alt bagt fra "
                   "bunden.\n\nSkriv 'FEST' + dato."),
         "cta": "Skriv 'FEST' + dato", "billede_hint": "Fad med wienerbrød og stykkager"},
        {"type": "catering-kvalitet",
         "tekst": ("Til den lille sammenkomst 🌿 Friskbagt surdejsbrød og boller, "
                   "en tapaskasse eller burgerpose, og wienerbrød til kaffen — alt "
                   "økologisk og bagt fra bunden.\n\nHar I noget på vej? Skriv 'FEST'."),
         "cta": "Skriv 'FEST'", "billede_hint": "Surdejsbrød, tapaskasse og wienerbrød på bord"},
    ],
    2: [  # Onsdag — inspiration til lejligheder
        {"type": "event-socialproof",
         "tekst": ("Et bord fyldt med friskbagt wienerbrød og kager gør enhver fest "
                   "lidt bedre 🍰 Croissant, kanelsnurrer, træstammer, napoleonshat "
                   "og hindbærsnitter — sammensat efter antal gæster.\n\n"
                   "Skriv 'FEST' + antal."),
         "cta": "Skriv 'FEST' + antal", "billede_hint": "Bord med wienerbrød og stykkager til fest"},
        {"type": "kagebord-inspiration",
         "tekst": ("Skal I have gæster i weekenden? 🥐 Forudbestil friskbagt "
                   "surdejsbrød, boller og wienerbrød, så I slipper for morgenkøen "
                   "og kan hygge jer i stedet.\n\nSkriv 'GÆSTER' + antal."),
         "cta": "Skriv 'GÆSTER' + antal", "billede_hint": "Morgenbord med friskbagt brød og wienerbrød"},
    ],
    3: [  # Torsdag — fødselsdage
        {"type": "foedselsdag",
         "tekst": ("Fødselsdag på vej? 🎉 Forudbestil friskbagt wienerbrød, boller "
                   "og kager — træstammer, hindbærsnitter, cookies og kokostoppe — "
                   "klar til afhentning.\n\nSkriv 'FØDSELSDAG' + dato."),
         "cta": "Skriv 'FØDSELSDAG' + dato", "billede_hint": "Fad med wienerbrød og stykkager til fødselsdag"},
        {"type": "boern-foedselsdag",
         "tekst": ("Børnefødselsdag i klassen eller derhjemme? 🧁 Vi pakker "
                   "teboller, boller og kager (træstammer, cookies, kokostoppe) klar "
                   "til afhentning — nemt for jer.\n\nSkriv 'BØRNEFEST' + antal."),
         "cta": "Skriv 'BØRNEFEST' + antal", "billede_hint": "Teboller og stykkager til børnefødselsdag"},
    ],
    4: [  # Fredag — weekendfester / arrangementer
        {"type": "weekend-fest",
         "tekst": ("Fest i weekenden? 🎉 Nå det endnu — forudbestil friskbagt "
                   "surdejsbrød, wienerbrød og kager til jeres gæster. Klar til "
                   "afhentning.\n\nSkriv 'FEST' + dato."),
         "cta": "Skriv 'FEST' + dato", "billede_hint": "Festbord med brød, wienerbrød og kager"},
        {"type": "reception-jubilaeum",
         "tekst": ("Reception, dåb eller jubilæum? 🥂 Vi står for det friskbagte — "
                   "wienerbrød, kager, surdejsbrød og boller, plus en tapaskasse til "
                   "det salte.\n\nSkriv 'ARRANGEMENT'."),
         "cta": "Skriv 'ARRANGEMENT'", "billede_hint": "Reception-bord med wienerbrød, kager og tapas"},
    ],
    5: [  # Lørdag — større arrangementer / brunch
        {"type": "stor-catering",
         "tekst": ("Stort arrangement på vej? 🎪 Fra få til mange gæster pakker vi "
                   "friskbagt surdejsbrød, boller, wienerbrød og kager klar til "
                   "afhentning — økologisk og bagt fra bunden.\n\n"
                   "Skriv 'ARRANGEMENT' + antal."),
         "cta": "Skriv 'ARRANGEMENT' + antal", "billede_hint": "Stort bord med brød, boller og wienerbrød"},
        {"type": "brunch-catering",
         "tekst": ("Skal I samle familie eller kolleger til brunch? 🥐 Forudbestil "
                   "friskbagt surdejsbrød, boller, wienerbrød og juice — klar til "
                   "afhentning.\n\nSkriv 'BRUNCH' + antal."),
         "cta": "Skriv 'BRUNCH' + antal", "billede_hint": "Brunchbord med brød, boller, wienerbrød og juice"},
    ],
    6: [  # Søndag — planlæg i god tid
        {"type": "planlaeg-lejlighed",
         "tekst": ("Har I en lejlighed på vej? 📅 Fødselsdag, konfirmation, "
                   "firmafest eller bare gæster — forudbestil det friskbagte i god "
                   "tid, så det står klar når I skal bruge det.\n\n"
                   "Skriv hvad I planlægger."),
         "cta": "Skriv hvad I planlægger", "billede_hint": "Kalender og friskbagt brød, planlægnings-stemning"},
        {"type": "book-tidligt",
         "tekst": ("Jo før vi ved det, jo bedre kan vi bage til jer 🥐 Forudbestil "
                   "surdejsbrød, boller, wienerbrød og kager til jeres dag.\n\n"
                   "Skriv 'FEST' eller 'FIRMA' + dato."),
         "cta": "Skriv 'FEST'/'FIRMA' + dato", "billede_hint": "Friskbagt brød og wienerbrød klar til afhentning"},
    ],
}

# ── Sæson-lejligheder (måned → et stærkt anlednings-opslag) ────────────────────
# Rammer folk mens de PLANLÆGGER — det er her de store ordrer skabes.
# Kun rigtige varer (ingen kransekage/lagkage).
_SAESON: Dict[int, Dict] = {
    1:  {"type": "saeson-nytaarskur",
         "tekst": ("Godt nytår! 🥂 Skal I have gæster i januar? Forudbestil "
                   "friskbagt wienerbrød, kager og surdejsbrød. Og planlægger I "
                   "allerede forårets konfirmation eller fest — så book datoen nu.\n\n"
                   "Skriv 'ARRANGEMENT'."),
         "cta": "Skriv 'ARRANGEMENT'", "billede_hint": "Friskbagt wienerbrød og kager, nytårsstemning"},
    2:  {"type": "saeson-konfirmation-tidlig",
         "tekst": ("Konfirmation til foråret? 💐 Nu er tiden at booke. Vi "
                   "forudbager wienerbrød, boller og kager til kaffebordet, så den "
                   "store dag er klaret — og de populære datoer i maj går hurtigt.\n\n"
                   "Skriv 'KONFIRMATION' + dato."),
         "cta": "Skriv 'KONFIRMATION' + dato", "billede_hint": "Kaffebord med wienerbrød og kager"},
    3:  {"type": "saeson-paaske",
         "tekst": ("Påskefrokost for familien eller kontoret? 🐣 Forudbestil "
                   "friskbagt surdejsbrød, boller, wienerbrød og juice, klar til "
                   "afhentning.\n\nSkriv 'PÅSKE' + antal."),
         "cta": "Skriv 'PÅSKE' + antal", "billede_hint": "Påskefrokostbord med surdejsbrød og boller"},
    4:  {"type": "saeson-konfirmation",
         "tekst": ("Konfirmationssæsonen er her 💐 Skal vi stå for det friskbagte "
                   "til kaffebordet? Wienerbrød, boller og kager, klar til "
                   "afhentning — økologisk og bagt fra bunden.\n\n"
                   "Skriv 'KONFIRMATION' + dato."),
         "cta": "Skriv 'KONFIRMATION' + dato", "billede_hint": "Kaffebord til konfirmation med wienerbrød og kager"},
    5:  {"type": "saeson-fest-forening",
         "tekst": ("Maj og juni er fyldt med konfirmationer og studenterfester 🎓 "
                   "Til studenten har vi studenterbrød (halv plade) + friskbagt "
                   "wienerbrød og boller til bordet.\n\nSkriv 'FEST' + dato."),
         "cta": "Skriv 'FEST' + dato", "billede_hint": "Studenterbrød og friskbagt til studenterfest"},
    6:  {"type": "saeson-sommerfest",
         "tekst": ("Sommerfest eller havefest på vej? ☀️ Forudbestil friskbagt "
                   "surdejsbrød, wienerbrød og kager — og en tapaskasse til det "
                   "salte.\n\nSkriv 'FEST' + antal."),
         "cta": "Skriv 'FEST' + antal", "billede_hint": "Havefest med brød, wienerbrød og tapaskasse"},
    7:  {"type": "saeson-sommer-arrangement",
         "tekst": ("Firmaskovtur eller sommer-sammenkomst? 🌳 Vi pakker friskbagt "
                   "surdejsbrød, boller og wienerbrød klar — plus en tapaskasse "
                   "eller burgerpose.\n\nSkriv 'ARRANGEMENT' + antal."),
         "cta": "Skriv 'ARRANGEMENT' + antal", "billede_hint": "Kurve med brød, boller og tapaskasse til udflugt"},
    8:  {"type": "saeson-opstart",
         "tekst": ("Ny sæson på kontoret? 🍂 Start op med friskbagt til møderne — "
                   "surdejsbrød, boller og wienerbrød. Og planlægger I allerede "
                   "julefrokosten, er det nu de gode datoer findes.\n\nSkriv 'FIRMA'."),
         "cta": "Skriv 'FIRMA'", "billede_hint": "Kontor-forplejning med surdejsbrød og wienerbrød"},
    9:  {"type": "saeson-hoestfest",
         "tekst": ("Efterårsfest eller rund fødselsdag? 🍂 Forudbestil friskbagt "
                   "wienerbrød, kager og surdejsbrød til gæsterne, klar til "
                   "afhentning.\n\nSkriv 'FEST' + dato."),
         "cta": "Skriv 'FEST' + dato", "billede_hint": "Efterårsbord med wienerbrød og kager"},
    10: {"type": "saeson-julefrokost-booking",
         "tekst": ("Julefrokosten planlægges nu 🎄 Skal vi stå for det friskbagte "
                   "— surdejsbrød, wienerbrød og kager — til jeres firma- eller "
                   "familiejulefrokost? De bedste december-datoer bookes allerede.\n\n"
                   "Skriv 'JULEFROKOST' + antal."),
         "cta": "Skriv 'JULEFROKOST' + antal", "billede_hint": "Julefrokostbord med surdejsbrød og wienerbrød"},
    11: {"type": "saeson-jul-tidlig",
         "tekst": ("December fylder hurtigt op 🎄 Skal I have gæster i julen? "
                   "Forudbestil friskbagt surdejsbrød, wienerbrød og kager i god "
                   "tid.\n\nSkriv 'JUL' + hvad I skal bruge."),
         "cta": "Skriv 'JUL' + ønske", "billede_hint": "Julehygge med wienerbrød og friskbagt brød"},
    12: {"type": "saeson-jul",
         "tekst": ("Julen er lig med godt bagværk 🎄 Friskbagt surdejsbrød til "
                   "frokosten, wienerbrød og kager til de søde juledage — bestil i "
                   "god tid, så det står klar når I skal bruge det.\n\n"
                   "Skriv 'JUL' + ønske."),
         "cta": "Skriv 'JUL' + ønske", "billede_hint": "Julebord med surdejsbrød og wienerbrød"},
}

_HASHTAGS = ("#OrganicMarketGreve #Greve #friskbagt #wienerbrød #økologisk "
             "#fødselsdag #konfirmation #forudbestilling")


# ── Deterministisk variant-valg ───────────────────────────────────────────────

def _vaelg_variant(d: date) -> Dict:
    varianter = _SKABELONER[d.weekday()]
    # Rotér på ugenummer så samme ugedag varierer uge for uge
    uge = d.isocalendar()[1]
    return varianter[uge % len(varianter)]


def _saeson_for_dato(d: date) -> Optional[Dict]:
    """Sæson-lejlighed for måneden (fx konfirmation i april, jul i december)."""
    return _SAESON.get(d.month)


def _vaelg_skabelon(d: date) -> Dict:
    """Vælg dagens opslag: sæson-lejlighed på planlægningsdage (ons/fre),
    ellers den evergreen ugedags-variant."""
    saeson = _saeson_for_dato(d)
    if saeson and d.weekday() in (2, 4):
        return saeson
    return _vaelg_variant(d)


# ── Data-drevet krydderi (fejler pænt hvis data mangler) ──────────────────────

def _data_tilfoejelse(skabelon: Dict, data: Optional[Dict]) -> str:
    """Tilføj en konkret data-linje når det giver mening (fx spild-trend)."""
    if not data:
        return ""
    try:
        # Hvis motoren får spild-serie kan søndags-antispild-opslag blive konkret
        if skabelon["type"] == "antispild":
            serie = data.get("spild_serie") or []
            faerdige = [u for u in serie if not u.get("indevaerende")]
            if len(faerdige) >= 4:
                snit = sum(u.get("netto_spild_kr", 0) or 0 for u in faerdige[-4:]) / 4
                if snit > 0:
                    return ("\n\n(Bag kulisserne: vi arbejder hver uge på at "
                            "presse spildet ned — hver bestilling i forvejen hjælper.)")
    except Exception:
        pass
    return ""


# ── Offentlig API ─────────────────────────────────────────────────────────────

def generer_opslag(dag: Optional[date] = None, data: Optional[Dict] = None,
                   brug_ai: bool = True) -> Dict:
    """Byg ét færdigt opslag til den givne dag (default: i dag).
    Returnerer {dato, type, tekst, cta, hashtags, billede_hint, ai}."""
    d = dag or date.today()
    skab = _vaelg_skabelon(d)

    tekst = skab["tekst"] + _data_tilfoejelse(skab, data)
    link = _link_for(skab["type"])
    if link:
        tekst = f"{tekst}\n\n👉 {skab['cta']} — eller se mere & bestil: {link}"
    else:
        tekst = f"{tekst}\n\n👉 {skab['cta']}"

    resultat = {
        "dato": d.isoformat(),
        "type": skab["type"],
        "tekst": tekst,
        "cta": skab["cta"],
        "hashtags": _HASHTAGS,
        "billede_hint": skab.get("billede_hint", ""),
        "ai": False,
    }

    if brug_ai and os.environ.get("ANTHROPIC_API_KEY"):
        forbedret = _ai_polish(resultat)
        if forbedret:
            resultat["tekst"] = forbedret
            resultat["ai"] = True

    return resultat


# Cache af egne opslag (stemme-reference) — hentes højst hver 6. time
_STIL_CACHE: Dict[str, object] = {"tid": 0.0, "eksempler": []}


def hent_stil_eksempler(antal: int = 25, max_alder_sek: int = 21600) -> List[str]:
    """Hent jeres egne seneste opslag fra Facebook som stemme-reference.
    Cachet i 6 timer. Returnerer liste af opslagstekster (tomme sprunget over).
    Tom liste hvis token ikke er sat eller kaldet fejler."""
    if not facebook_konfigureret():
        return []
    nu = time.time()
    if _STIL_CACHE["eksempler"] and (nu - float(_STIL_CACHE["tid"])) < max_alder_sek:
        return list(_STIL_CACHE["eksempler"])  # type: ignore
    page_id = os.environ.get("FB_PAGE_ID")
    token = os.environ.get("FB_PAGE_TOKEN")
    try:
        import requests
        r = requests.get(
            f"https://graph.facebook.com/v21.0/{page_id}/published_posts",
            params={"fields": "message", "limit": antal, "access_token": token},
            timeout=20,
        )
        j = r.json()
        data = j.get("data", []) if isinstance(j, dict) else []
        tekster = [d.get("message", "").strip() for d in data if d.get("message", "").strip()]
        if tekster:
            _STIL_CACHE["tid"] = nu
            _STIL_CACHE["eksempler"] = tekster
        return tekster
    except Exception:
        return []


def _ai_polish(opslag: Dict) -> Optional[str]:
    """Lad Claude finpudse opslaget så det ikke bliver skabelon-agtigt.
    Bruger jeres egne opslag som stemme-reference når de kan hentes.
    Returnerer None ved fejl (så vi falder tilbage til skabelonen)."""
    try:
        import anthropic as _ant
        client = _ant.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        # Hent jeres egne opslag som stil-eksempler (few-shot stemme-efterligning)
        eksempler = hent_stil_eksempler()
        stil_blok = ""
        if eksempler:
            # Tag op til 8 af de mest relevante (længde 40–600 tegn) som eksempler
            udvalg = [e for e in eksempler if 40 <= len(e) <= 600][:8] or eksempler[:8]
            numre = "\n\n".join(f"[{i+1}] {e}" for i, e in enumerate(udvalg))
            stil_blok = (
                "\n\nHER ER HVORDAN VI SELV SKRIVER PÅ FACEBOOK (efterlign nøjagtigt "
                "denne stemme, tone, længde, emoji-brug og måde at åbne/afslutte på):\n"
                + numre + "\n"
            )

        prompt = (
            "Du er social media-ansvarlig for Organic Market Greve — en "
            "økologisk købmand, café og bageri på " + ADRESSE + ". Finpuds "
            "nedenstående Facebook-opslag så det lyder varmt, lokalt og "
            "menneskeligt — ikke som reklame. Behold budskab, call-to-action, "
            "linket og længden (max ~600 tegn). Behold 1-3 relevante emojis."
            + stil_blok +
            "\nSvar KUN med den færdige opslagstekst, intet andet.\n\n"
            "OPSLAG DER SKAL FINPUDSES:\n---\n"
            + opslag["tekst"]
        )
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        txt = (msg.content[0].text or "").strip()
        return txt or None
    except Exception:
        return None


# ── Facebook-publicering (gated bag token) ────────────────────────────────────

def facebook_konfigureret() -> bool:
    return bool(os.environ.get("FB_PAGE_ID") and os.environ.get("FB_PAGE_TOKEN"))


def publicer_paa_facebook(tekst: str) -> Dict:
    """Publicér et tekst-opslag på Facebook-siden via Graph API.
    Returnerer {ok, post_id|fejl}. Gør intet hvis token ikke er sat."""
    page_id = os.environ.get("FB_PAGE_ID")
    token = os.environ.get("FB_PAGE_TOKEN")
    if not (page_id and token):
        return {"ok": False, "fejl": "FB_PAGE_ID / FB_PAGE_TOKEN ikke sat — publicering slået fra"}
    try:
        import requests
        r = requests.post(
            f"https://graph.facebook.com/v21.0/{page_id}/feed",
            data={"message": tekst, "access_token": token},
            timeout=30,
        )
        j = r.json()
        if r.status_code == 200 and j.get("id"):
            return {"ok": True, "post_id": j["id"]}
        fejl = j.get("error", {}).get("message", r.text[:200])
        return {"ok": False, "fejl": fejl}
    except Exception as e:
        return {"ok": False, "fejl": str(e)[:200]}
