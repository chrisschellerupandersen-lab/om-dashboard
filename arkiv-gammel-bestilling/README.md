# Arkiv — gammel bestillings-/ingest-proces (pensioneret 1/9-2026)

Disse scripts er **stoppet og annulleret** i forbindelse med skiftet til
Organic Bakery + bageri-portalen pr. 1/9-2026. De må **ikke køres mere**.
Historiske data de har lagt i basen bliver stående (kun ny-import er stoppet).

## Hvad erstattede dem

| Gammelt script | Erstattet af |
|---|---|
| `bestilling_sync.py`, `gdrive_bestilling_import.py`, `bestilling_parser.py` (Google Drive Excel-bestilling → dashboard) | **Portal-mail-importeren**: `portal_ordre_parser.py` + endpoint `/api/bageri/ordre-mail` + Gmail-scriptet `gmail-portal-ordre.gs`. Bestillingen laves nu i bageri-portalen; ordrebekræftelsen "Ordren #NNNN er bekræftet" fra "Min butik" fylder automatisk ugens bestilling ind. |
| `bager_retur_sync.py`, `bager_gmail_import.py` (retur/retur-fakturaer fra gammel bager) | **Intet** — Organic Bakery tager **ingen retur**. Spild spores nu direkte (bestilt − solgt − reddet). |
| `tgtg_sync.py` (TGTG dagligt salg) | **Intet** — TGTG udgår 1/9-2026. |

## Manuel oprydning der stadig skal gøres (uden for koden)

- **Gmail Apps Script:** slet evt. triggere for retur/TGTG/bestilling hvis de fandtes.
  (Salgsrapport-scriptet `gmail-webhook-komplet.gs` KØRER VIDERE — det er jeres salgsdata.)
- **Windows Task Scheduler / cron:** slet evt. planlagte kørsler af ovenstående scripts.
- **Aktivér den nye:** indsæt `gmail-portal-ordre.gs` i Apps Script og kør `opretPortalTrigger` én gang.
