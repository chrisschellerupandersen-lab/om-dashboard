---
name: azure-sql-daily-sp
description: Kører en stored procedure på Azure SQL hver hverdag kl. 06:00 baseret på config-fil.
---

Du skal afvikle en stored procedure på en Azure SQL-database. Følg disse trin præcist:

## 1. Mount task-mappen
Task-mappen ligger på brugerens OneDrive på host-stien:
`C:\Users\ChrisSchellerupAnder\OneDrive - Toolpack Solutions ApS\Dokumenter\Claude\Scheduled\azure-sql-daily-sp`

Kald `mcp__cowork__request_cowork_directory` med netop den path for at få adgang til den i denne session. Noter den returnerede VM-path (typisk `/sessions/<session-id>/mnt/azure-sql-daily-sp`).

## 2. Læs konfiguration
Læs filen `config.json` i den monterede mappe. Den indeholder:
- `server` (fx "minserver.database.windows.net")
- `database`
- `username`
- `password`
- `stored_procedure` (fuldt kvalificeret navn, fx "dbo.MinProcedure")
- `parameters` (objekt med evt. parametre til SP'en, kan være tomt)
- `timeout_seconds` (query timeout)
- `log_to_file` (true/false)

Hvis nogen af placeholder-værdierne stadig står i filen ("MINSERVER", "MINDATABASE", "MIN_SQL_BRUGER", "MIT_PASSWORD", "dbo.MinStoredProcedure"): STOP og skriv en klar besked om at brugeren skal udfylde config.json først. Kør IKKE videre.

## 3. Installér drivere
Kør i bash:
```
pip install pymssql --break-system-packages --quiet
```

## 4. Afvikl stored procedure
Skriv et Python-script der:
1. Indlæser konfigurationen fra config.json (brug den VM-path der blev returneret i trin 1).
2. Forbinder til Azure SQL via `pymssql.connect(server, user, password, database, timeout=timeout_seconds, login_timeout=30)`.
3. Kalder den stored procedure med `cursor.callproc(stored_procedure, tuple(parameters.values()))` hvis der er parametre, ellers `cursor.execute("EXEC " + stored_procedure)`.
4. Committer (`conn.commit()`).
5. Fanger evt. resultset med `cursor.fetchall()` hvis der er rækker — ellers rapporterer antal berørte rækker.
6. Lukker forbindelsen pænt.
7. Håndterer fejl og rapporterer dem tydeligt (SQL-fejlkode, besked).

## 5. Log resultatet
Hvis `log_to_file` er true: append en linje til `log.txt` i den monterede task-mappe (samme mappe som config.json) med:
- Tidsstempel (ISO 8601, lokal tid)
- Status: SUCCESS eller FAILED
- Stored procedure-navn
- Antal berørte rækker eller fejlbesked
- Varighed i sekunder

## 6. Firewall / netværk
Hvis forbindelsen fejler med en besked om firewall / IP ikke tilladt: rapporter det tydeligt, og forklar at Azure SQL-firewallen skal tillade sandbox-IP'en. Foreslå enten at slå "Allow Azure services and resources to access this server" til, eller whitelist den specifikke IP (aflæs via `curl ifconfig.me`).

## 7. Opsummér
Afslut med en kort opsummering: kørte SP'en OK, hvor lang tid tog det, hvad blev logget.

Succes-kriterium: Stored procedure blev kaldt uden fejl, eller der blev rapporteret en klar fejlbesked med årsag.