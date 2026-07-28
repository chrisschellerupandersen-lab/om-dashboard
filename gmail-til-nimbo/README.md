# Gmail → Nimbo Regnskab

Uploader leverandørfakturaer (PDF) fra Gmail-indbakken automatisk til
[Nimbo Regnskab](https://regnskab-production.up.railway.app), hvor AI læser dem og
foreslår kontering. Bilaget lander som **"afventer"** — det bogføres eller betales
aldrig automatisk, kun efter din godkendelse i Nimbo.

## Hvad den gør præcist
- Kigger kun i **indbakken** (åbne, ikke-behandlede mails)
- Kun fra afsendere du selv angiver i `AFSENDERE`
- Kun mails **med en PDF-vedhæftning**
- Springer dem over der allerede har labelet **`nimbosendt`**
- Efter upload sætter den labelet **`nimbosendt`** → uploades aldrig to gange

Ingen manuel labeling nødvendig — den kører på afsender + indbakke.

## Opsætning (engangs, ~5 min)
Se de detaljerede trin øverst i [`forward_faktura.gs`](forward_faktura.gs). Kort:
1. https://script.google.com → Nyt projekt → indsæt `forward_faktura.gs` → gem.
2. Udfyld `INGEST_TOKEN` (findes i Railway → regnskab-projektet → Variables →
   `INGEST_TOKEN`) og `AFSENDERE` med dine leverandørers mailadresser.
3. Kør `uploadInvoices` én gang → godkend Gmail-adgang.
4. Tilføj en **tidsudløser** (fx hver 30. min).

## Første kørsel
Den uploader ALLE åbne fakturaer fra `AFSENDERE` i indbakken uden `nimbosendt`.
Er nogle allerede bogført i Nimbo, så sæt `nimbosendt` på dem først (så springes de over).

## Sikkerhed
Endpointet i Nimbo kræver en delt hemmelig token (`INGEST_TOKEN`) i en header —
uden korrekt token afvises uploadet. Skift token i Railway hvis den nogensinde
lækkes (fx ved et uheld i scriptet), og opdatér den her.
