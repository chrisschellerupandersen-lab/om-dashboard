import sqlite3
import os
from typing import List, Dict, Any, Optional

DB_PATH = os.environ.get("DB_PATH", "dashboard.db")


def _conn() -> sqlite3.Connection:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS uploads (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                indlæst_dato TEXT    DEFAULT (datetime('now', 'localtime')),
                rapport_dato TEXT
            );

            CREATE TABLE IF NOT EXISTS transaktioner (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                dato        TEXT    NOT NULL,
                varenummer  TEXT    DEFAULT '',
                varenavn    TEXT    DEFAULT '',
                kategori    TEXT    DEFAULT '',
                antal       REAL    DEFAULT 0,
                omsætning   REAL    DEFAULT 0,
                kostpris    REAL    DEFAULT 0,
                avance      REAL    DEFAULT 0,
                avance_pct  REAL    DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_trans_dato ON transaktioner(dato);
            CREATE INDEX IF NOT EXISTS idx_trans_vare ON transaktioner(varenavn);
        """)


def gem_transaktioner(rapport_dato: str, transaktioner: List[Dict]) -> int:
    with _conn() as conn:
        # Filen er kumulativ fra startdato — erstat alt eksisterende data
        conn.execute("DELETE FROM transaktioner")
        conn.execute("DELETE FROM uploads")

        cur = conn.execute(
            "INSERT INTO uploads (rapport_dato) VALUES (?)",
            (rapport_dato,)
        )
        upload_id = cur.lastrowid

        conn.executemany("""
            INSERT INTO transaktioner
                (dato, varenummer, varenavn, kategori, antal, omsætning, kostpris, avance, avance_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                t["dato"],
                t.get("varenummer", ""),
                t.get("varenavn", ""),
                t.get("kategori", ""),
                t.get("antal", 0),
                t.get("omsætning", 0),
                t.get("kostpris", 0),
                t.get("avance", 0),
                t.get("avance_pct", 0),
            )
            for t in transaktioner
        ])

    return upload_id


def hent_seneste_snapshot_info() -> Optional[Dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, rapport_dato, indlæst_dato FROM uploads ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def hent_dashboard_data() -> Dict:
    with _conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM transaktioner").fetchone()[0]
        if count == 0:
            return {
                "daglig_omsætning": [],
                "top_produkter": [],
                "kpi": {},
                "senest_opdateret": None,
            }

        # Daglig omsætning — direkte fra transaktionsdatoer
        daglig = conn.execute("""
            SELECT dato, SUM(omsætning) AS omsætning
            FROM   transaktioner
            GROUP  BY dato
            ORDER  BY dato ASC
        """).fetchall()

        # Top 10 produkter
        top = conn.execute("""
            SELECT varenavn,
                   SUM(omsætning) AS total_omsætning,
                   SUM(antal)     AS total_antal
            FROM   transaktioner
            GROUP  BY varenavn
            ORDER  BY total_omsætning DESC
            LIMIT  10
        """).fetchall()

        # Seneste dag
        seneste_dato = conn.execute(
            "SELECT MAX(dato) FROM transaktioner"
        ).fetchone()[0]

        seneste_dag_omsætning = conn.execute(
            "SELECT COALESCE(SUM(omsætning), 0) FROM transaktioner WHERE dato = ?",
            (seneste_dato,)
        ).fetchone()[0]

        # Totaler
        totaler = conn.execute("""
            SELECT COALESCE(SUM(omsætning), 0)        AS omsætning,
                   COALESCE(SUM(avance),    0)        AS avance,
                   COUNT(DISTINCT varenavn)           AS antal_varer
            FROM   transaktioner
        """).fetchone()

        avance_pct = 0.0
        if totaler["omsætning"] > 0:
            avance_pct = (totaler["avance"] / totaler["omsætning"]) * 100

        senest = conn.execute(
            "SELECT indlæst_dato FROM uploads ORDER BY id DESC LIMIT 1"
        ).fetchone()

    return {
        "daglig_omsætning": [
            {"dato": r["dato"], "omsætning": round(r["omsætning"], 2)}
            for r in daglig
        ],
        "top_produkter": [
            {
                "varenavn":  r["varenavn"] or "Ukendt",
                "omsætning": round(r["total_omsætning"], 2),
                "antal":     round(r["total_antal"], 1),
            }
            for r in top
        ],
        "kpi": {
            "seneste_dag_omsætning": round(seneste_dag_omsætning, 2),
            "total_omsætning":       round(totaler["omsætning"], 2),
            "avance_pct":            round(avance_pct, 1),
            "antal_varer":           totaler["antal_varer"],
            "seneste_rapport_dato":  seneste_dato,
        },
        "senest_opdateret": senest["indlæst_dato"] if senest else None,
    }
