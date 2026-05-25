"""
run_sp.py – Kør Azure SQL stored procedure lokalt.
Læser config.json fra samme mappe som dette script.
"""

import json
import os
import time
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.json"
LOG_PATH = SCRIPT_DIR / "log.txt"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        raw = f.read().replace("\x00", "").strip()
    return json.loads(raw)


def check_placeholders(config):
    placeholders = {"MINSERVER", "MINDATABASE", "MIN_SQL_BRUGER", "MIT_PASSWORD", "dbo.MinStoredProcedure"}
    for key, val in config.items():
        if str(val) in placeholders:
            print(f"STOP: config.json indeholder stadig placeholder-værdien '{val}' i feltet '{key}'.")
            print("Udfyld config.json med dine rigtige værdier og prøv igen.")
            sys.exit(1)


def log(log_path, line):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    try:
        import pymssql
    except ImportError:
        print("pymssql er ikke installeret. Kør: pip install pymssql")
        sys.exit(1)

    config = load_config()
    check_placeholders(config)

    server           = config["server"]
    database         = config["database"]
    username         = config["username"]
    password         = config["password"]
    stored_procedure = config["stored_procedure"]
    parameters       = config.get("parameters", {})
    timeout_seconds  = config.get("timeout_seconds", 300)
    log_to_file      = config.get("log_to_file", False)

    print(f"[{datetime.now().isoformat(timespec='seconds')}] Forbinder til {server} / {database}")
    print(f"Stored procedure: {stored_procedure}")

    start_time = time.time()
    status     = "FAILED"
    result_msg = ""

    try:
        conn = pymssql.connect(
            server=server,
            user=username,
            password=password,
            database=database,
            timeout=timeout_seconds,
            login_timeout=30,
        )
        cursor = conn.cursor()

        if parameters:
            cursor.callproc(stored_procedure, tuple(parameters.values()))
        else:
            cursor.execute(f"EXEC {stored_procedure}")

        # Prøv at hente resultset
        try:
            rows = cursor.fetchall()
            if rows:
                result_msg = f"{len(rows)} rows returned"
                print(f"Resultat ({len(rows)} rækker):")
                for row in rows[:50]:
                    print(" ", row)
                if len(rows) > 50:
                    print(f"  ... og {len(rows) - 50} flere rækker")
            else:
                rc = cursor.rowcount
                result_msg = f"{rc if rc >= 0 else 0} rows affected"
                print(result_msg)
        except Exception:
            result_msg = f"SP afsluttet. rowcount={cursor.rowcount}"
            print(result_msg)

        conn.commit()
        conn.close()
        status = "SUCCESS"
        print(f"✅ {status}")

    except pymssql.OperationalError as e:
        result_msg = f"OperationalError: {e}"
        print(f"❌ FEJL: {result_msg}")
        err_lower = str(e).lower()
        if any(kw in err_lower for kw in ["firewall", "not allowed", "cannot open", "unable to connect"]):
            print("\n⚠️  Mulig firewall-blokering. Tjek Azure Portal → SQL-server → Networking")
            print("   og tilføj din lokale IP til firewall-reglerne.")

    except pymssql.Error as e:
        result_msg = f"SQL Error {e.args[0] if e.args else ''}: {e}"
        print(f"❌ FEJL: {result_msg}")

    except Exception as e:
        result_msg = f"{type(e).__name__}: {e}"
        print(f"❌ FEJL: {result_msg}")

    duration  = round(time.time() - start_time, 2)
    timestamp = datetime.now().isoformat(timespec="seconds")

    print(f"Varighed: {duration}s")

    if log_to_file:
        log_line = f"{timestamp} | {status} | {stored_procedure} | {result_msg} | {duration}s"
        log(LOG_PATH, log_line)
        print(f"Logget til: {LOG_PATH}")

    sys.exit(0 if status == "SUCCESS" else 1)


if __name__ == "__main__":
    main()
