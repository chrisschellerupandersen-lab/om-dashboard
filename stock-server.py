"""
Ratepension Live Stock Data Server
Henter live data fra Yahoo Finance og udstiller som REST API
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf
import json
from datetime import datetime, timedelta
import threading
import time

app = Flask(__name__)
CORS(app)

# Din portefølje
PORTFOLIO = [
    # Top aktier
    {"ticker": "MSFT", "name": "Microsoft", "shares": 30},
    {"ticker": "TSLA", "name": "Tesla", "shares": 31},
    {"ticker": "NVDA", "name": "NVIDIA", "shares": 51},
    {"ticker": "GOOGL", "name": "Alphabet", "shares": 26},
    {"ticker": "NVO", "name": "Novo Nordisk B", "shares": 189},
    {"ticker": "KO", "name": "Coca-Cola", "shares": 84},
    {"ticker": "SBUX", "name": "Starbucks", "shares": 62},
    {"ticker": "META", "name": "Meta", "shares": 8},
    {"ticker": "AAPL", "name": "Apple", "shares": 16},
    {"ticker": "COIN", "name": "Coinbase", "shares": 20},

    # Øvrige aktier
    {"ticker": "V", "name": "Visa Inc.", "shares": 9},
    {"ticker": "O", "name": "Realty Income", "shares": 48},
    {"ticker": "SAAB-B.ST", "name": "SAAB B", "shares": 44},
    {"ticker": "KTOS", "name": "Kratos Defense", "shares": 42},
    {"ticker": "SG", "name": "Strategy A", "shares": 14},
    {"ticker": "SPOT", "name": "Spotify", "shares": 3},
    {"ticker": "SOUN", "name": "SoundHound AI", "shares": 218},
    {"ticker": "SOFI", "name": "SoFi Technologies", "shares": 47},
    {"ticker": "CYBN", "name": "Cybin", "shares": 27},

    # ETF
    {"ticker": "EUNL.DE", "name": "iShares MSCI World ETF", "shares": 125},

    # Derivater (SKAL LUKKES!)
    {"ticker": "BULL.NOVO.X3", "name": "BULL NOVO X3 ND1", "shares": 198, "warning": True},
    {"ticker": "BULL.NFLX.X2", "name": "BULL NFLX X2 ND", "shares": 62, "warning": True},
]

# Cache for data
cache = {
    "stocks": {},
    "last_update": None,
    "update_interval": 300  # 5 minutter
}

def get_stock_data(ticker):
    """Henter data for en enkelt aktie"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        # Hent historisk data for år-performance
        hist = stock.history(period="1y")

        current_price = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0)

        if len(hist) > 0:
            year_ago_price = hist.iloc[0]["Close"]
            year_change = ((current_price - year_ago_price) / year_ago_price * 100) if year_ago_price > 0 else 0
        else:
            year_change = 0

        return {
            "ticker": ticker,
            "price": round(current_price, 2),
            "currency": info.get("currency", "USD"),
            "change_day": round(info.get("regularMarketChangePercent", 0), 2),
            "change_year": round(year_change, 2),
            "market_cap": info.get("marketCap", 0),
            "pe_ratio": info.get("trailingPE", 0),
            "dividend_yield": round(info.get("dividendYield", 0) * 100, 2) if info.get("dividendYield") else 0,
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh", 0),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow", 0),
        }
    except Exception as e:
        print(f"Error fetching {ticker}: {str(e)}")
        return {
            "ticker": ticker,
            "price": 0,
            "error": str(e)
        }

def update_cache():
    """Opdater cache med live data"""
    print(f"[{datetime.now()}] Henter live data fra Yahoo Finance...")

    stocks_data = []
    for stock in PORTFOLIO:
        ticker = stock["ticker"]
        data = get_stock_data(ticker)
        data["name"] = stock["name"]
        data["shares"] = stock["shares"]
        if stock.get("warning"):
            data["warning"] = True
        stocks_data.append(data)

    cache["stocks"] = stocks_data
    cache["last_update"] = datetime.now().isoformat()
    print(f"[{datetime.now()}] Data opdateret succesfuldt! ({len(stocks_data)} aktier)")

def background_updater():
    """Opdater data i baggrund hver 5 minutter"""
    while True:
        update_cache()
        time.sleep(cache["update_interval"])

# Start background thread
updater_thread = threading.Thread(target=background_updater, daemon=True)
updater_thread.start()

@app.route("/api/portfolio", methods=["GET"])
def get_portfolio():
    """Returnerer hele porteføljen med live data"""

    if not cache["stocks"]:
        update_cache()  # First load

    # Beregn totaler
    total_value = 0
    total_shares_value = 0

    for stock in cache["stocks"]:
        if "price" in stock and stock["price"] > 0:
            value = stock["price"] * stock["shares"]
            total_shares_value += value

    return jsonify({
        "portfolio": cache["stocks"],
        "last_update": cache["last_update"],
        "total_shares": len(cache["stocks"]),
        "update_interval": cache["update_interval"],
        "summary": {
            "total_value_usd": round(total_shares_value, 2),
            "total_value_dkk": round(total_shares_value * 6.8, 2),
            "number_of_stocks": len(cache["stocks"]),
            "warning_count": len([s for s in cache["stocks"] if s.get("warning")])
        }
    })

@app.route("/api/stock/<ticker>", methods=["GET"])
def get_stock(ticker):
    """Returnerer data for en specifik aktie"""
    data = get_stock_data(ticker)
    return jsonify(data)

@app.route("/api/portfolio/<category>", methods=["GET"])
def get_portfolio_category(category):
    """Returnerer kategori af portefølje"""

    if not cache["stocks"]:
        update_cache()

    if category == "top":
        # Top 10 efter værdi
        sorted_stocks = sorted(
            cache["stocks"],
            key=lambda x: (x.get("price", 0) * x.get("shares", 0)),
            reverse=True
        )
        return jsonify(sorted_stocks[:10])

    elif category == "warnings":
        # Aktier med advarsler (derivater)
        warnings = [s for s in cache["stocks"] if s.get("warning")]
        return jsonify(warnings)

    elif category == "gainers":
        # Bedste performers i dag
        sorted_stocks = sorted(
            cache["stocks"],
            key=lambda x: x.get("change_day", 0),
            reverse=True
        )
        return jsonify(sorted_stocks[:5])

    elif category == "losers":
        # Værste performers i dag
        sorted_stocks = sorted(
            cache["stocks"],
            key=lambda x: x.get("change_day", 0)
        )
        return jsonify(sorted_stocks[:5])

    else:
        return jsonify({"error": "Unknown category"}), 400

@app.route("/api/refresh", methods=["POST"])
def manual_refresh():
    """Manuelt opdater data nu (i stedet for at vente 5 min)"""
    update_cache()
    return jsonify({
        "status": "success",
        "last_update": cache["last_update"],
        "stocks_count": len(cache["stocks"])
    })

@app.route("/health", methods=["GET"])
def health():
    """Health check"""
    return jsonify({
        "status": "ok",
        "last_update": cache["last_update"],
        "stocks_cached": len(cache["stocks"])
    })

@app.route("/", methods=["GET"])
def index():
    """Info side"""
    return jsonify({
        "name": "Ratepension Live Data Server",
        "version": "1.0",
        "endpoints": {
            "GET /api/portfolio": "Hele porteføljen med live data",
            "GET /api/stock/<ticker>": "Data for én aktie",
            "GET /api/portfolio/top": "Top 10 aktier efter værdi",
            "GET /api/portfolio/warnings": "Aktier med advarsler (derivater)",
            "GET /api/portfolio/gainers": "Top 5 bedste i dag",
            "GET /api/portfolio/losers": "Top 5 værste i dag",
            "POST /api/refresh": "Manuelt opdater nu",
            "GET /health": "Server status"
        },
        "last_update": cache["last_update"],
        "update_interval_seconds": cache["update_interval"]
    })

if __name__ == "__main__":
    import os

    print("🚀 Ratepension Live Data Server starter...")
    print("📊 Henter live data fra Yahoo Finance...")

    # Initial load
    update_cache()

    # Railway bruger PORT miljøvariabel, localhost bruger 5000
    port = int(os.environ.get("PORT", 5000))
    host = "0.0.0.0"  # Railway kræver 0.0.0.0, ikke 127.0.0.1

    print(f"\n✅ Server klar på port {port}!")
    print(f"📡 API dokumentation: http://localhost:{port}/")
    print("🔄 Data opdateres automatisk hver 5 minutter")
    print(f"📱 Dashboard kalder: http://localhost:{port}/api/portfolio\n")

    app.run(debug=False, port=port, host=host)