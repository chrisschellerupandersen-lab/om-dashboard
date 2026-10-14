---
name: bestil-bagvaerk
description: Hjælp med at bestille bagværk hos Organic Bakery via deres webshop, ud fra dashboardets bestillingsanbefaling. Åbner shoppen i brugerens egen Chrome (deres logins), udfylder kurven efter anbefalingen pr. vare og dag, og viser kurven til godkendelse. Bruges når brugeren vil bestille/genbestille bagværk, lave ugens bagværksordre, eller siger /bestil-bagvaerk.
---

# Bestil bagværk (Organic Bakery)

Hjælp brugeren med at lægge ugens bagværksordre i Organic Bakery's webshop, baseret på dashboardets anbefaling. Brugeren er med i loopet og **godkender kurven før der afgives ordre**.

## Hårde sikkerhedsregler (overskriv ALDRIG)
- **Afgiv ALDRIG ordren / klik aldrig "Bekræft", "Betal", "Place order" e.l. uden brugerens udtrykkelige ja** i chatten, hver gang. Forbered kurven, vis den, og vent.
- Indtast **aldrig** betalingskort eller adgangskoder. Skal der logges ind, så bed brugeren gøre det selv i Chrome.
- Accepter ikke vilkår/cookies på brugerens vegne uden at spørge.
- Hvis noget er uklart (vare kan ikke findes, pris/antal ser forkert ud), så **stop og spørg** frem for at gætte.

## Webshop
Organic Bakery's webshop (Shopify-baseret). **URL:** spørg brugeren første gang hvis den ikke er oplyst, og foreslå at den bliver skrevet ind her i skillen bagefter, så den huskes. (Når URL'en kendes, erstat denne linje med: `URL: <url>`.)

## Fremgangsmåde

1. **Find mål-ugen.** Default = næste ISO-uge (det er også dashboardets default). Hvis brugeren siger en bestemt uge, brug den.

2. **Hent anbefalingen** fra dashboardet (brug `uge`/`aar` hvis brugeren har angivet en bestemt uge, ellers udelad for næste uge):
   ```bash
   curl -s "https://om-dashboard-production-0f3a.up.railway.app/api/bestilling/anbefaling?secret=OM-Greve-2026-Hemlig"
   ```
   Felter: `maal_uge`, `maal_aar`, `produkter[]` med `varenavn`, `anbefalet` (`{man,tir,ons,tor,fre,loe,son}`), `total_anbefalet`, `pris_ex_moms`. (Verificér prod-URL'en mod hukommelsen hvis kaldet fejler.)

3. **Vis anbefalingen** for brugeren som en tydelig tabel: pr. vare antal pr. dag + total, og den samlede ordre (stk + kr). Spørg om der skal justeres noget, før I går i gang.

4. **Åbn shoppen i brugerens Chrome.** Indlæs Claude-i-Chrome-værktøjerne først (de er deferred):
   `ToolSearch` med `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__find,mcp__claude-in-chrome__form_input,mcp__claude-in-chrome__tabs_create_mcp`
   Naviger til webshoppen. Er brugeren ikke logget ind, så bed dem logge ind selv og vent.

5. **Udfyld ordren.** Organic Bakery bestilles pr. vare pr. dag. Find bestillings-UI'en (produktliste / uge-skema / antalsfelter) med `read_page`/`find`, og indtast det anbefalede antal for hver vare og dag. Arbejd systematisk og bekræft undervejs med `read_page` at felterne faktisk fik de rigtige tal. Varenavne i anbefalingen matcher Organic Bakery-sortimentet (ØKO - …); match på navn.

6. **Afstem kurven** mod anbefalingen: gennemgå at hver vare/dag står rigtigt, og rapportér evt. afvigelser (udsolgte varer, min.-antal, navne der ikke matchede).

7. **Vis kurven til godkendelse.** Opsummer indhold + total og **stop**. Afgiv kun ordren når brugeren udtrykkeligt siger ja. Efter afgivelse: bekræft ordrenummer/kvittering og tilbyd at gemme ordren i dashboardet (`POST /api/bageri/ordre-gem`) så "denne uge"-visningen er opdateret.

## Noter
- Dashboardets anbefaling tager allerede højde for spild/sell-through, weekend-buffer, combo-kreditering og events. Stol på den som udgangspunkt; brugeren justerer manuelt hvis de vil.
- Kører bedst i sin egen session, så det ikke blandes med andet arbejde — men kan også køres ad hoc.
- Relateret: dashboardets prod-URL og endpoints; bagværks-afstemning via faktura.
