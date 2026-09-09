# 📊 Ratepension Dashboard - Setup Guide

Live aktiedata for din ratepension på Nordea.

---

## 🚀 **OPTION 1: Railway Deployment (ANBEFALET)**

Du bruger Railway allerede — så gør det her:

### Trin 1: Upload til GitHub
```bash
git add stock-server.py requirements.txt Procfile
git commit -m "Add: Live stock data server for ratepension"
git push
```

### Trin 2: Deploy på Railway
1. Gå til https://railway.app
2. Klik **New Project** → **Deploy from GitHub**
3. Vælg dit repository
4. Railway opretter en service automatisk
5. Tjek den genererede **Public URL** (f.eks. `https://ratepension-prod-abc123.railway.app`)

### Trin 3: Opdater Dashboard
I `ratepension-dashboard.html`, find denne linje:
```javascript
const API_URL = "http://localhost:5000/api/portfolio";
```

Erstat med din Railway URL:
```javascript
const API_URL = "https://ratepension-prod-abc123.railway.app/api/portfolio";
```

✅ Done! Dashboard henter nu live data fra din Railway server.

---

## 🏠 **OPTION 2: Lokalt (Til test)**

Hvis du vil teste lokalt før Railway:

### Trin 1: Installer dependencies
```bash
pip install -r requirements.txt
```

### Trin 2: Start serveren
```bash
python stock-server.py
```

Du ser:
```
🚀 Ratepension Live Data Server starter...
✅ Server klar! Åben http://localhost:5000
📡 API dokumentation: http://localhost:5000/
```

### Trin 3: Åben dashboard
- Åben `ratepension-dashboard.html` i browser
- Dashboard henter data fra `http://localhost:5000`

---

## 📡 **API Endpoints**

Serveren udstiller disse endpoints:

| Endpoint | Metode | Beskrivelse |
|----------|--------|-------------|
| `/api/portfolio` | GET | Hele porteføljen med live data |
| `/api/stock/<ticker>` | GET | Data for én aktie (f.eks. `/api/stock/MSFT`) |
| `/api/portfolio/top` | GET | Top 10 aktier efter værdi |
| `/api/portfolio/warnings` | GET | Aktier med advarsler (derivater) |
| `/api/portfolio/gainers` | GET | Top 5 bedste i dag |
| `/api/portfolio/losers` | GET | Top 5 værste i dag |
| `/api/refresh` | POST | Manuelt opdater data nu |
| `/health` | GET | Server status |

### Eksempel API svar:
```json
{
  "portfolio": [
    {
      "ticker": "MSFT",
      "name": "Microsoft",
      "shares": 30,
      "price": 418.65,
      "change_day": -0.34,
      "change_year": 45.2
    },
    ...
  ],
  "last_update": "2026-09-09T14:30:00",
  "summary": {
    "total_value_usd": 700000,
    "total_value_dkk": 4760000
  }
}
```

---

## 🔧 **Maintenance**

### Tilføj nye aktier
Edit `stock-server.py`, find `PORTFOLIO` list:
```python
PORTFOLIO = [
    {"ticker": "MSFT", "name": "Microsoft", "shares": 30},
    # Tilføj din nye aktie her:
    {"ticker": "DSV", "name": "DSV", "shares": 10},
]
```

Commit og push — Railway redeploy automatisk.

### Fjern derivater
Find disse linjer og slet dem:
```python
{"ticker": "BULL.NOVO.X3", "name": "BULL NOVO X3 ND1", "shares": 198, "warning": True},
{"ticker": "BULL.NFLX.X2", "name": "BULL NFLX X2 ND", "shares": 62, "warning": True},
```

---

## ⚠️ **Troubleshooting**

### "Kan ikke forbinde til server"
- **Lokalt:** Tjek at `python stock-server.py` køres
- **Railway:** Tjek at URL i dashboard matcher din Railway domain
- **CORS:** Serveren understøtter CORS — skulle ikke være problem

### "Aktier vises ikke i tabel"
- Åben browser console (F12) og se fejlmeddelelser
- Tjek at API URL er korrekt
- Server skal have internettilgang for yfinance

### Server går ned / genstartes
- Railway har "Always On" option (check project settings)
- Logfiler kan ses i Railway dashboard
- Yfinance har rate limits — 1 request/aktie per 5 min er sikkert

---

## 📊 **Data Updates**

- **Automatisk:** Hvert 5 minut (baggrund)
- **Manuelt:** Klik **🔄 Opdater** knap i dashboard
- **API:** POST `/api/refresh` for omgående update

Serveren bruger yfinance, som giver 15-20 min delayed data (gratis tier).

---

## 🎯 **Næste Steps**

1. ✅ Deploy server på Railway
2. ✅ Opdater dashboard URL
3. ✅ Test dashboard (skal vise live data)
4. ✅ **Lukk derivaterne** (BULL NOVO, BULL NFLX) snarest
5. ✅ Rebalancer portefølje efter guide i dashboard

---

## 💡 **Tips**

- **Performance:** Dashboard cacher data lokalt (browser localStorage) — ingen problem
- **Omkostninger:** Railway gratis tier covers dette fint (< 1000 API calls/dag)
- **Sikkerhed:** Server udstiller offentligt API — ingen sensitiv data (kun tal på aktier)
- **Udvidelse:** Let at tilføje alerts, emails, eller Slack notifications senere

---

Har du spørgsmål? Kig i `/health` endpoint eller Rails logs!
