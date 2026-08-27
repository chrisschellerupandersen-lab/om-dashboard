"""
Parser for Organic Bakery portal-ordrebekræftelser (Shopify-mail fra "Min butik").

Bestillingen laves i bageri-portalen (organicbakery.dk) og bekræftes med en mail
i Gmail: emne "Ordren #NNNN er bekræftet", afsender store+106786619717@t.shopifyemail.com.

Mailen har INGEN uge-nummer — kun ugedage. Da der bestilles i ugen før (typisk
onsdag/torsdag) til den KOMMENDE uge, udledes målugen som ugen der starter på den
førstkommende mandag efter bestillingsdatoen.

Varelinje-format i mail-body (plain text):
    ØKO - <Produkt> × <antal>
    <Ugedag>
    <pris> kr

Samme produkt optræder én linje pr. ugedag; parseren aggregerer til man..søn.
"""
import re
from datetime import date, timedelta
from typing import Dict, List, Optional

# Bageri-portalens Shopify-afsender (må IKKE forveksles med "Organic Market B2B",
# store+81368744278@…, som er noget helt andet).
PORTAL_AFSENDER = "store+106786619717@t.shopifyemail.com"

_UGEDAG = {
    "mandag": "man", "tirsdag": "tir", "onsdag": "ons", "torsdag": "tor",
    "fredag": "fre", "lørdag": "loe", "loerdag": "loe", "søndag": "son", "sondag": "son",
}

# Normalisering af portal-navne → kanoniske varenavne (så spild/kategori matcher).
# Ukendte navne beholdes som de er (renset), så nye varer ikke tabes.
_NAVN_MAP = {
    "gulerodskage (1 person)":        "Gulerodskage 1 pers",
    "gulerodskage (5-6 personer)":    "Gulerodskage 5-6 pers",
    "cookie":                         "Cookie",
    "pain au chocolate":              "Pain au Chocolate",
    "tebirkes":                       "Tebirkes",
    "tebolle":                        "Tebolle alm",
    "tebolle med choko":              "Tebolle m. chokolade",
    "karamelliseret croissant":       "Karamelliseret croissant",
    "crossiant":                      "Croissant",
    "croissant":                      "Croissant",
    "kanelsnurre":                    "Kanelsnurre",
    "kardemommesnurre":               "Kardemommesnurre",
    "softkernerugbrød 900g":          "Softkernerugbrød",
    "softkernerugbrod 900g":          "Softkernerugbrød",
    "8x12 surdejs focaccia":          "Focaccia",
    "surdejsbolle med birkes":        "Surdejsbolle m. birkes",
    "surdejsbolle med sesam":         "Surdejsbolle m. sesam",
    "surdejsbolle":                   "Surdejsbolle",
    "surdejsbrød med sesam":          "Surdejsbrød m. sesam",
    "surdejsbrod med sesam":          "Surdejsbrød m. sesam",
    "surdejsbrød":                    "Surdejsbrød",
    "surdejsbrod":                    "Surdejsbrød",
}

# "ØKO - Produkt × 15"  /  "ØKO Gulerodskage (1 person) × 15"
_VARE_RE = re.compile(r"^ØKO\s*-?\s*(.+?)\s*[×xX]\s*(\d+)\s*$")
_PRIS_RE = re.compile(r"^([\d.]+),(\d{2})\s*kr", re.IGNORECASE)
_ORDRE_RE = re.compile(r"Ordre\s*#\s*(\d+)", re.IGNORECASE)


def _rens_navn(raw: str) -> str:
    n = re.sub(r"\s+", " ", raw).strip()
    return _NAVN_MAP.get(n.lower(), n)


def _maal_uge(mail_dato: str) -> tuple:
    """Målugen = ugen der starter på førstkommende mandag efter bestillingsdatoen.
    Bestilles i uge N-1 til levering i uge N."""
    d = date.fromisoformat(mail_dato[:10])
    naeste_mandag = d + timedelta(days=(7 - d.weekday()) or 7)
    iso = naeste_mandag.isocalendar()
    return iso[0], iso[1], naeste_mandag.isoformat()


def parse_ordre_mail(body: str, mail_dato: str,
                     override_uge: Optional[int] = None,
                     override_aar: Optional[int] = None) -> Dict:
    """Parser en portal-ordrebekræftelse til {ordre_nr, uge, aar, linjer[]}.
    linjer matcher gem_ugebestilling(): varenavn, pris_ex_moms, man..son,
    total_antal, total_pris."""
    linjer_txt = [l.strip() for l in body.replace("\r", "").split("\n")]

    ordre_nr = None
    m = _ORDRE_RE.search(body)
    if m:
        ordre_nr = m.group(1)

    aar, uge, maal_mon = _maal_uge(mail_dato)
    if override_uge:
        uge = int(override_uge)
    if override_aar:
        aar = int(override_aar)

    # Aggregér pr. produkt: {navn: {"dage": {man..son: antal}, "kr": total_pris}}
    agg: Dict[str, Dict] = {}
    i = 0
    n = len(linjer_txt)
    while i < n:
        vm = _VARE_RE.match(linjer_txt[i])
        if not vm:
            i += 1
            continue
        navn = _rens_navn(vm.group(1))
        antal = int(vm.group(2))
        # Find næste ikke-tomme linje = ugedag, og derefter pris
        dag = None
        pris = 0.0
        j = i + 1
        while j < n and j <= i + 6:
            t = linjer_txt[j]
            if not t:
                j += 1
                continue
            if dag is None:
                dag = _UGEDAG.get(t.lower())
                j += 1
                continue
            pm = _PRIS_RE.match(t)
            if pm:
                pris = float(pm.group(1).replace(".", "")) + float(pm.group(2)) / 100.0
            break
        if dag:
            rec = agg.setdefault(navn, {"dage": {}, "kr": 0.0})
            rec["dage"][dag] = rec["dage"].get(dag, 0) + antal
            rec["kr"] += pris
        i = j + 1 if dag else i + 1

    DAGE = ["man", "tir", "ons", "tor", "fre", "loe", "son"]
    linjer: List[Dict] = []
    for navn, rec in agg.items():
        dage = rec["dage"]
        total_antal = sum(dage.values())
        pris_stk = round(rec["kr"] / total_antal, 2) if total_antal else 0.0
        linjer.append({
            "varenavn":     navn,
            "varenummer":   "",
            "pris_ex_moms": pris_stk,
            **{d: dage.get(d, 0) for d in DAGE},
            "total_antal":  total_antal,
            "total_pris":   round(rec["kr"], 2),
        })
    linjer.sort(key=lambda x: x["varenavn"])
    return {"ordre_nr": ordre_nr, "uge": uge, "aar": aar,
            "maal_mandag": maal_mon, "linjer": linjer,
            "total_stk": sum(l["total_antal"] for l in linjer),
            "total_kr":  round(sum(l["total_pris"] for l in linjer), 2)}
