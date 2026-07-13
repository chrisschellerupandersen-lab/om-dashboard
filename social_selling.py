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

_SKABELONER: Dict[int, List[Dict]] = {
    0: [  # Mandag — virksomheder / B2B
        {"type": "b2b-moede",
         "tekst": ("Skal I holde møde eller event i denne uge? 🤝 Lad os stå for "
                   "forplejningen — friskbagt økologisk brød, sødt og rigtig kaffe, "
                   "klar til afhentning eller levering.\n\nVirksomheder i Greve: "
                   "skriv 'FIRMA' + antal personer, så sender vi en menu."),
         "cta": "Skriv 'FIRMA' + antal personer", "billede_hint": "Flot mødebakke med brød, kaffe og kage"},
        {"type": "firma-fast",
         "tekst": ("Faste morgenbrød-aftaler til kontoret ☕🥐 Bestilt i forvejen, "
                   "friskbagt og klar fra kl. 06 — uden at nogen hos jer skal tænke "
                   "på det.\n\nSkriv 'FIRMA', så laver vi en aftale til jeres hus."),
         "cta": "Skriv 'FIRMA'", "billede_hint": "Kontor får leveret friskbagt om morgenen"},
    ],
    1: [  # Tirsdag — håndværk/kvalitet knyttet til lejligheder
        {"type": "kage-haandvaerk",
         "tekst": ("En lagkage til festen skal smage af noget. 🎂 Vi bager fra "
                   "bunden med økologiske råvarer — fyld, bunde og pynt efter jeres "
                   "ønske. Perfekt til fødselsdagen eller den store dag.\n\n"
                   "Skriv 'KAGE' + dato, så finder vi den rette."),
         "cta": "Skriv 'KAGE' + dato", "billede_hint": "Hjemmelavet lagkage, nærbillede af pynt"},
        {"type": "catering-kvalitet",
         "tekst": ("Catering med rigtig smag. 🌿 Til jeres arrangement laver vi "
                   "det søde og det salte af friske, økologiske råvarer — ikke "
                   "noget fra en fjern fabrik.\n\nHar I noget på vej? Skriv 'FEST', "
                   "så snakker vi menu."),
         "cta": "Skriv 'FEST'", "billede_hint": "Indbydende catering-opstilling, økologiske råvarer"},
    ],
    2: [  # Onsdag — social proof fra lejligheder + inspiration
        {"type": "event-socialproof",
         "tekst": ("Sidste weekend leverede vi kagebord til en rund fødselsdag her "
                   "i Greve — og bordet var tomt inden gæsterne var færdige med at "
                   "rose det. 🎉🎂\n\nSkal vi gøre jeres næste fest lidt lettere? "
                   "Skriv 'FEST', så hjælper vi."),
         "cta": "Skriv 'FEST'", "billede_hint": "Fyldt kagebord til fest, glade gæster"},
        {"type": "kagebord-inspiration",
         "tekst": ("Sådan kan et kagebord se ud til jeres fest 🎂🍰 Blandet efter "
                   "anledning og antal — fra intim fødselsdag til stort arrangement. "
                   "Alt bagt fra bunden.\n\nSkriv 'KAGEBORD', så sætter vi et sammen "
                   "til jer."),
         "cta": "Skriv 'KAGEBORD'", "billede_hint": "Overdådigt kagebord ovenfra"},
    ],
    3: [  # Torsdag — fødselsdage / kager
        {"type": "foedselsdag",
         "tekst": ("Fødselsdag på vej? 🎂 Lad os bage kagen, så du kan nyde dagen. "
                   "Lagkage, kagemand eller et helt kagebord — friskbagt og efter "
                   "jeres ønske.\n\nSkriv 'FØDSELSDAG' + dato, så er den sag klaret."),
         "cta": "Skriv 'FØDSELSDAG' + dato", "billede_hint": "Festlig fødselsdagslagkage med lys"},
        {"type": "boern-foedselsdag",
         "tekst": ("Børnefødselsdag i klassen eller derhjemme? 🧁 Vi pakker "
                   "kagemand, boller og det hele klar til afhentning — nemt for jer, "
                   "en fest for dem.\n\nSkriv 'BØRNEFEST' + antal, så klarer vi resten."),
         "cta": "Skriv 'BØRNEFEST' + antal", "billede_hint": "Kagemand og boller til børnefødselsdag"},
    ],
    4: [  # Fredag — weekendfester / arrangementer
        {"type": "weekend-fest",
         "tekst": ("Fest i weekenden? 🎉 Nå det endnu — vi kan stadig bage "
                   "kagebord, brød og catering til jeres arrangement. Friskt, "
                   "økologisk og klar til afhentning.\n\nSkriv 'FEST' + dato, så "
                   "finder vi ud af det."),
         "cta": "Skriv 'FEST' + dato", "billede_hint": "Festbord dækket med bagværk og catering"},
        {"type": "reception-jubilaeum",
         "tekst": ("Reception, dåb eller jubilæum? 🥂 Vi står for det søde og det "
                   "salte, så I kan koncentrere jer om gæsterne. Alt bagt fra bunden "
                   "med økologiske råvarer.\n\nSkriv 'ARRANGEMENT', så snakker vi menu."),
         "cta": "Skriv 'ARRANGEMENT'", "billede_hint": "Elegant reception-opstilling med kransekage/kage"},
    ],
    5: [  # Lørdag — store arrangementer / brunch-catering
        {"type": "stor-catering",
         "tekst": ("Stort arrangement på vej? 🎪 Fra 20 til 200 gæster pakker vi "
                   "brød, kagebord og catering klar — friskbagt og økologisk, uden "
                   "at I skal løfte en finger i køkkenet.\n\nSkriv 'ARRANGEMENT' + "
                   "antal, så laver vi et tilbud."),
         "cta": "Skriv 'ARRANGEMENT' + antal", "billede_hint": "Stort cateringbord til mange gæster"},
        {"type": "brunch-catering",
         "tekst": ("Skal I samle familie eller kolleger til brunch? 🍳🥐 Vi pakker "
                   "det hele klar — friskbagt brød, sødt og det til at fylde bordet. "
                   "\n\nSkriv 'BRUNCH' + antal, så står det klar."),
         "cta": "Skriv 'BRUNCH' + antal", "billede_hint": "Overdådigt brunchbord til en gruppe"},
    ],
    6: [  # Søndag — planlæg i god tid
        {"type": "planlaeg-lejlighed",
         "tekst": ("Har I en lejlighed på vej? 📅 Fødselsdag, konfirmation, "
                   "firmafest eller reception — de bedste datoer bookes først, og "
                   "bagt i god tid bliver det bare bedre.\n\nSkriv hvad I planlægger, "
                   "så holder vi en plads til jer."),
         "cta": "Skriv hvad I planlægger", "billede_hint": "Kalender + kage, planlægnings-stemning"},
        {"type": "book-tidligt",
         "tekst": ("Den gode kage til den store dag starter med en besked. 🎂 Jo "
                   "før vi ved det, jo mere kan vi skræddersy til jer — og jo "
                   "sikrere er jeres dato.\n\nSkriv 'KAGE' eller 'FEST' + dato, så "
                   "er I i gang."),
         "cta": "Skriv 'KAGE'/'FEST' + dato", "billede_hint": "Bager pynter kage til bestilling"},
    ],
}

# ── Sæson-lejligheder (måned → et stærkt anlednings-opslag) ────────────────────
# Rammer folk mens de PLANLÆGGER — det er her de store ordrer skabes.
_SAESON: Dict[int, Dict] = {
    1:  {"type": "saeson-nytaarskur",
         "tekst": ("Godt nytår! 🥂 Skal I holde nytårskur eller reception i "
                   "januar, står vi klar med kransekage, kagebord og catering. "
                   "Og planlægger I allerede forårets konfirmation eller fest — så "
                   "book datoen nu.\n\nSkriv 'ARRANGEMENT', så er I i gang."),
         "cta": "Skriv 'ARRANGEMENT'", "billede_hint": "Kransekage og bobler, nytårsstemning"},
    2:  {"type": "saeson-konfirmation-tidlig",
         "tekst": ("Konfirmation til foråret? 💐 Nu er tiden at booke kagebordet. "
                   "Vi bager lagkager, boller og det hele fra bunden — og de "
                   "populære datoer i maj går hurtigt.\n\nSkriv 'KONFIRMATION' + "
                   "dato, så holder vi pladsen."),
         "cta": "Skriv 'KONFIRMATION' + dato", "billede_hint": "Festligt konfirmations-kagebord"},
    3:  {"type": "saeson-paaske",
         "tekst": ("Påskefrokost for familien eller kontoret? 🐣 Vi pakker "
                   "friskbagt brød, sødt og det salte klar til jeres bord.\n\n"
                   "Skriv 'PÅSKE' + antal, så står det klar til afhentning."),
         "cta": "Skriv 'PÅSKE' + antal", "billede_hint": "Påskefrokostbord med bagværk"},
    4:  {"type": "saeson-konfirmation",
         "tekst": ("Konfirmationssæsonen er her 💐 Skal vi stå for kagebordet, så I "
                   "kan nyde dagen med familien? Lagkager, boller og det hele — "
                   "friskbagt og økologisk.\n\nSkriv 'KONFIRMATION' + dato, så laver "
                   "vi et tilbud."),
         "cta": "Skriv 'KONFIRMATION' + dato", "billede_hint": "Konfirmations-kagebord, forårsstemning"},
    5:  {"type": "saeson-fest-forening",
         "tekst": ("Maj og juni er fyldt med konfirmationer, studenterfester og "
                   "runde fødselsdage 🎓🎉 Skal vi bage til jeres? De gode "
                   "weekender bookes lige nu.\n\nSkriv 'FEST' + dato, så holder vi "
                   "pladsen."),
         "cta": "Skriv 'FEST' + dato", "billede_hint": "Studenterfest/konfirmation med kagebord"},
    6:  {"type": "saeson-sommerfest",
         "tekst": ("Sommerfest, bryllup eller havefest på vej? ☀️🎉 Vi laver "
                   "kagebord og catering, der holder til en lang sommeraften — "
                   "friskt og økologisk.\n\nSkriv 'FEST' + antal, så snakker vi menu."),
         "cta": "Skriv 'FEST' + antal", "billede_hint": "Sommerhavefest med cateringbord"},
    7:  {"type": "saeson-sommer-arrangement",
         "tekst": ("Firmaskovtur, familiefest eller sommer-sammenkomst? 🌳 Vi "
                   "pakker friskbagt og catering klar til jeres udflugt eller fest.\n\n"
                   "Skriv 'ARRANGEMENT' + antal, så er I klar."),
         "cta": "Skriv 'ARRANGEMENT' + antal", "billede_hint": "Cateringkurve til sommerudflugt"},
    8:  {"type": "saeson-opstart",
         "tekst": ("Ny sæson på kontoret? 🍂 Start op med friskbagt til møderne "
                   "eller en lille firmafrokost. Og planlægger I allerede "
                   "julefrokosten — så er det nu, de gode datoer findes.\n\nSkriv "
                   "'FIRMA', så laver vi en aftale."),
         "cta": "Skriv 'FIRMA'", "billede_hint": "Kontor-opstart med friskbagt forplejning"},
    9:  {"type": "saeson-hoestfest",
         "tekst": ("Høstfest, firmaevent eller rund fødselsdag i efteråret? 🍂🎉 "
                   "Vi står for kagebord og catering, så I kan hygge jer med "
                   "gæsterne.\n\nSkriv 'FEST' + dato, så finder vi ud af det."),
         "cta": "Skriv 'FEST' + dato", "billede_hint": "Efterårsfest med kagebord"},
    10: {"type": "saeson-julefrokost-booking",
         "tekst": ("Julefrokosten planlægges nu 🎄 Skal vi stå for det søde og det "
                   "salte til jeres firma- eller familiejulefrokost? De bedste "
                   "december-datoer bookes allerede.\n\nSkriv 'JULEFROKOST' + antal, "
                   "så holder vi pladsen."),
         "cta": "Skriv 'JULEFROKOST' + antal", "billede_hint": "Julefrokostbord med bagværk og kransekage"},
    11: {"type": "saeson-jul-tidlig",
         "tekst": ("December fylder hurtigt op 🎄 Julefrokoster, receptioner og "
                   "kagebord til de søde juledage — book jeres nu, så vi kan bage "
                   "det perfekt til jer.\n\nSkriv 'JUL' + hvad I skal bruge."),
         "cta": "Skriv 'JUL' + ønske", "billede_hint": "Julehygge med kransekage og bagværk"},
    12: {"type": "saeson-jul",
         "tekst": ("Julen er lig med godt bagværk 🎄 Kransekage til nytår, "
                   "kagebord til juledagene, brød til den store frokost — bestil i "
                   "god tid, så det står klar når I skal bruge det.\n\nSkriv 'JUL' + "
                   "ønske, så klarer vi resten."),
         "cta": "Skriv 'JUL' + ønske", "billede_hint": "Julebord med kransekage og friskbagt"},
}

_HASHTAGS = ("#OrganicMarketGreve #Greve #kagebord #catering #fødselsdag "
             "#konfirmation #økologisk #festienemmere")


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
