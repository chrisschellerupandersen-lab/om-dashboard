# Organic Bakery — produkt → URL / SKU kort

Kortlagt 20-10-2026. Webshoppens Shopify-handles er GENBRUGT fra gamle produkter og
matcher IKKE varenavnet (fx Croissant = `oko-hvedebrod-m-birkes`). Brug dette kort i
stedet for at gætte. Hvis en slug fejler (404), så søg i stedet varen frem via shoppens
søgefunktion eller via kurvens søgefelt ("Tilføj produkter efter navn, SKU eller stregkode").

| Vare (dashboard-navn) | Produkt-URL-slug (`/products/<slug>`) | Kurv-SKU-præfiks | Risikogruppe |
|---|---|---|---|
| Surdejsbolle | oko-kernebolle | PROD-07 | standard |
| Surdejsbolle m. sesam | oko-musliboller | PROD-10 | standard |
| Müsli surdejsbolle | oko-musli-surdejsbolle | PROD-23 | standard |
| Surdejsbrød | oko-signatur-brod | PROD-11 | standard |
| Surdejsbrød m. sesam | oko-ciabattaflute | PROD-01 | standard |
| Softkernerugbrød | oko-softkernerugbrod-1400g | PROD-13 | standard |
| Tebolle alm | oko-tebolle | PROD-18 | standard |
| Tebirkes | oko-softkernerugbrod-1000g | PROD-12 | risiko (premium) |
| Croissant | oko-hvedebrod-m-birkes | PROD-06 | risiko (premium) |
| Pain au Chocolate | oko-landbrod | PROD-09 | risiko (premium) |
| Kanelsnurre | oko-kanelsnurre | PROD-19 | standard |
| Kardemommesnurre | oko-kernebrod | PROD-08 | kun fre/lør/søn |
| ~~Brunsvigersnegl~~ (UDGÅET okt-2026) | oko-brunsvigersnegl | PROD-24 | erstattet af PROD-25 |
| Tebolle m. æblekompot & remonce | (erstatter Brunsvigersnegl) | PROD-25 | standard · 12 kr/stk · alle dage |
| Tebolle m. chokolade | oko-tebolle-med-choko | PROD-17 | kun fre/lør/søn |
| Cookie | oko-cookie | PROD-16 | kun ons/fre/søn |
| Gulerodskage 5-6 pers | oko-cheesecake-lemon-5-6-personer | PROD-15 | KUN tir+fre (OBS-tekst på siden) |
| Bananbrød | oko-bananbrod | PROD-26 | kage |

**Dag-koder i SKU-suffiks:** `-01`=Mandag, `-02`=Tirsdag, `-03`=Onsdag, `-04`=Torsdag,
`-05`=Fredag, `-06`=Lørdag, `-07`=Søndag. Så fx `PROD-12-06` = Tebirkes lørdag.

## Varer der IKKE tilbydes alle dage (webshoppen viser kun nogle dage)
- **Kardemommesnurre:** kun fre/lør/søn.
- **Tebolle m. chokolade:** kun fre/lør/søn.
- **Cookie:** kun ons/fre/søn.
- **Gulerodskage (5-6 pers):** KUN tirsdag + fredag (rød OBS-tekst på produktsiden).
- **Focaccia:** UDGÅET — bestil aldrig.

Når planen har antal på en dag varen ikke tilbydes, så spring den dag over og notér det i afstemningen.

## Indtastnings-UI (SparkLayer B2B)
- Hvert produkt bestilles på sin egen produktside: et uge-skema (Mandag..Søndag) med et antalsfelt pr. dag.
- Skemaets lodrette placering flytter sig efter hvor mange linjer varenavnet fylder (1, 2 eller 3 linjer) → tag ALTID et screenshot af produktsiden først og aflæs y-position pr. dag.
- Udfyld felt: `triple_click` på antalsfeltet + skriv tallet; commit ved at klikke et NEUTRALT, klik-sikkert sted (IKKE "Related products" nederst — det navigerer væk). Klik så **"Tilføj ordre"**.
- Efter "Tilføj ordre" nulstilles skemaet til 0 = varen er lagt i den server-side B2B-kurv. En kort toast "Tilføjet ordre" vises.
- **Pas på dobbelt-tilføjelse:** hvis du tvivler på om en vare kom med, så verificér via kurven (nedenfor) — tilføj ALDRIG "for en sikkerheds skyld" igen, det lægger oveni.

## Kurven / afstemning (autoritativ kilde)
- Kurven ligger server-side hos SparkLayer under konto-panelet: naviger til `https://organicbakery.dk/account`, klik fanen **"Min ordre"** → "Min indkøbsordre" med alle linjer + sub-total ("N linjer, M items").
- Linjelisten ligger i shadow DOM. Aflæs alle linjer med javascript_tool der traverserer shadow roots og matcher `ØKO - <navn> … SKU: PROD-xx-xx | Dag: <dag> … <antal>`.
- Ret en linje med linjens −/+ steppere (klik ét ad gangen, vent ~2s mellem klik — hurtige klik debounces og tæller kun ét) eller skraldespands-ikon for at slette.
- Konto bør være **Linda Greve / Organic Market Greve / greve@organicmarket.dk**. Er der logget en anden ind, eller er der ikke logget ind → STOP og giv besked.
