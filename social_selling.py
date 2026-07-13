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
import hashlib
from datetime import date, datetime
from typing import Dict, List, Optional

BESTIL_LINK = os.environ.get(
    "SOCIAL_BESTIL_LINK",
    "https://om-dashboard-production-0f3a.up.railway.app",
)
ADRESSE = "Greve Strandvej 20"

# ── Opslags-skabeloner pr. ugedag (0=mandag … 6=søndag) ───────────────────────
# Hver ugedag har flere varianter; motoren vælger deterministisk ud fra datoen,
# så samme dag altid giver samme opslag, men uge for uge varierer.

_SKABELONER: Dict[int, List[Dict]] = {
    0: [  # Mandag — B2B / mødeforplejning
        {"type": "b2b-morgen",
         "tekst": ("Ny uge, fyldt kalender? ☕🥐 Vi pakker friskbagt morgenbrød "
                   "og kaffe klar til afhentning fra kl. 06 — bestilt aftenen før, "
                   "klar når I møder ind.\n\nKontorer i Greve: skriv 'MORGEN' i en "
                   "besked, så laver vi en fast aftale til jeres hus."),
         "cta": "Skriv 'MORGEN' i en besked", "billede_hint": "Bakke med friskbagt brød + kaffe, morgenlys"},
        {"type": "b2b-moede",
         "tekst": ("Møde i denne uge? 🤝 Lad os stå for forplejningen: friskbagt "
                   "økologisk brød og rigtig kaffe, klar til afhentning kl. 06 — "
                   "uden binding.\n\nSkriv antal personer i en besked, så sender "
                   "vi en lille menu."),
         "cta": "Skriv antal personer", "billede_hint": "Mødebord med brød, kaffe og friske råvarer"},
    ],
    1: [  # Tirsdag — bag-om / friskbagt
        {"type": "bagom",
         "tekst": ("Kl. 05:12 er ovnen tændt. 🌅 De første økologiske boller er "
                   "på vej ud, og duften siger god morgen til hele huset.\n\nAlt "
                   "bages friskt hver dag her på " + ADRESSE + " — klar fra kl. 06."),
         "cta": "Kom forbi og mærk forskellen", "billede_hint": "Bager ved ovnen tidlig morgen, damp fra brødet"},
        {"type": "haandvaerk",
         "tekst": ("Godt brød har ingen genveje. 🥖 Vores dej hviler natten over, "
                   "så den får smag og skorpe som den skal — økologisk mel, tid "
                   "og håndværk.\n\nFriskbagt hver morgen på " + ADRESSE + "."),
         "cta": "Smag forskellen i dag", "billede_hint": "Nærbillede af sprød brødskorpe"},
    ],
    2: [  # Onsdag — social proof / midtuge-tilbud
        {"type": "socialproof",
         "tekst": ("\"Vores kunder tror, vi har hyret en privatkok\" 😄 — sådan "
                   "lyder det fra et af de kontorer i Greve, der nu får friskbagt "
                   "og kaffe klar hver uge.\n\nVil I have det samme til jeres møder? "
                   "Skriv 'MØDE', så sender vi en menu."),
         "cta": "Skriv 'MØDE'", "billede_hint": "Glad medarbejder med kaffe og brød på kontoret"},
        {"type": "midtuge",
         "tekst": ("Midt i ugen fortjener en pause der smager af noget. ☕ Kig "
                   "forbi til en frisk kop kaffe og dagens bagværk — vi står klar "
                   "på " + ADRESSE + "."),
         "cta": "Tag en midtuge-pause hos os", "billede_hint": "Kaffe og kage på cafébord"},
    ],
    3: [  # Torsdag — kaffe / hverdagspause
        {"type": "kaffe",
         "tekst": ("Torsdagens kaffe smager bedst, når nogen andre har bagt til "
                   "den. 🥐☕ Kig forbi og få en rigtig god kop + noget frisk fra "
                   "ovnen.\n\nVi er her på " + ADRESSE + "."),
         "cta": "Kom forbi efter kaffe", "billede_hint": "Latte med flot mælkeskum, hyggeligt café-hjørne"},
        {"type": "forudbestil-weekend",
         "tekst": ("Weekenden nærmer sig 🌿 Skal der friskbagt brød på bordet "
                   "lørdag morgen? Bestil i forvejen, så står det klar — og du "
                   "slipper for køen.\n\nSkriv 'WEEKEND' i en besked."),
         "cta": "Skriv 'WEEKEND'", "billede_hint": "Weekend-morgenbord med brød, æg og juice"},
    ],
    4: [  # Fredag — weekend-trafik
        {"type": "weekend",
         "tekst": ("Fredag! 🎉 I morgen smager bedst med friskbagt brød og en "
                   "kop kaffe, som du ikke selv skulle brygge. Kig forbi " + ADRESSE +
                   " i weekenden — vi har bagt til dig."),
         "cta": "Vi ses i weekenden", "billede_hint": "Fredagsstemning, brød og kaffe to-go"},
        {"type": "weekend-familie",
         "tekst": ("Weekend betyder god tid og godt brød. 🥖 Tag familien med "
                   "forbi til brunch-råvarer, friskbagt og økologisk godt fra "
                   "hylderne.\n\n" + ADRESSE + " — åbent i weekenden."),
         "cta": "Tag familien med forbi", "billede_hint": "Familie ved brunchbord, friske råvarer"},
    ],
    5: [  # Lørdag — brunch / oplevelse
        {"type": "brunch",
         "tekst": ("God lørdag! 🌞 Der er dækket op med friskbagt, økologiske "
                   "råvarer og kaffe der er værd at stå op til. Kom forbi " + ADRESSE +
                   " og gør weekenden lidt bedre."),
         "cta": "Kom til lørdags-brunch", "billede_hint": "Indbydende brunchopstilling"},
    ],
    6: [  # Søndag — værdi / madspild / ro
        {"type": "oekologi",
         "tekst": ("Søndagsro og rene råvarer. 🌱 Vi tror på økologi, håndværk "
                   "og at bage efter det, der bliver spist — ikke smidt ud. Derfor "
                   "bager vi efter bestilling og passer på både smag og klode.\n\n"
                   "Skal vi bage til dig i næste uge? Skriv 'I MORGEN' + hvad du vil have."),
         "cta": "Skriv 'I MORGEN' + ønske", "billede_hint": "Rolige økologiske råvarer, søndagsstemning"},
        {"type": "antispild",
         "tekst": ("Vi bager efter bestilling for at undgå madspild — så jo "
                   "tidligere du bestiller til i morgen, jo sikrere er din. 🌍🥐\n\n"
                   "Skriv 'I MORGEN' + dit ønske, så står det klar."),
         "cta": "Skriv 'I MORGEN' + ønske", "billede_hint": "Friskbagt på hylde, 'bagt efter bestilling'"},
    ],
}

_HASHTAGS = "#OrganicMarketGreve #Greve #økologi #friskbagt #bæredygtigt #lokalt"


# ── Deterministisk variant-valg ───────────────────────────────────────────────

def _vaelg_variant(d: date) -> Dict:
    varianter = _SKABELONER[d.weekday()]
    # Rotér på ugenummer så samme ugedag varierer uge for uge
    uge = d.isocalendar()[1]
    return varianter[uge % len(varianter)]


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
    skab = _vaelg_variant(d)

    tekst = skab["tekst"] + _data_tilfoejelse(skab, data)
    tekst = f"{tekst}\n\n👉 {skab['cta']} — eller bestil på {BESTIL_LINK}"

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


def _ai_polish(opslag: Dict) -> Optional[str]:
    """Lad Claude finpudse opslaget så det ikke bliver skabelon-agtigt.
    Returnerer None ved fejl (så vi falder tilbage til skabelonen)."""
    try:
        import anthropic as _ant
        client = _ant.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        prompt = (
            "Du er social media-ansvarlig for Organic Market Greve — en "
            "økologisk købmand, café og bageri på " + ADRESSE + ". Finpuds "
            "nedenstående Facebook-opslag så det lyder varmt, lokalt og "
            "menneskeligt — ikke som reklame. Behold budskab, call-to-action, "
            "linket og længden (max ~600 tegn). Behold 1-3 relevante emojis. "
            "Svar KUN med den færdige opslagstekst, intet andet.\n\n---\n"
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
