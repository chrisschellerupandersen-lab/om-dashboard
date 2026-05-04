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
            CREATE TABLE IF NOT EXISTS snapshots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                indlæst_dato TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                rapport_dato TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS produkter (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                varenummer  TEXT    DEFAULT '',
                varenavn    TEXT    DEFAULT '',
                antal       REAL    DEFAULT 0,
                omsætning   REAL    DEFAULT 0,
                kostpris    REAL    DEFAULT 0,
                avance      REAL    DEFAULT 0,
                avance_pct  REAL    DEFAULT 0,
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
            );

            CREATE INDEX IF NOT EXISTS idx_produkter_snapshot ON produkter(snapshot_id);
            CREATE INDEX IF NOT EXISTS idx_snapshots_dato     ON snapshots(rapport_dato);
        """)


def gem_snapshot(rapport_dato: str, produkter: List[Dict]) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO snapshots (rapport_dato) VALUES (?)",
            (rapport_dato,)
        )
        snapshot_id = cur.lastrowid

        conn.executemany("""
            INSERT INTO produkter
                (snapshot_id, varenummer, varenavn, antal, omsætning, kostpris, avance, avance_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                snapshot_id,
                p.get("varenummer", ""),
                p.get("varenavn", ""),
                p.get("antal", 0),
                p.get("omsætning", 0),
                p.get("kostpris", 0),
                p.get("avance", 0),
                p.get("avance_pct", 0),
            )
            for p in produkter
        ])

    return snapshot_id


def hent_seneste_snapshot_info() -> Optional[Dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, rapport_dato, indlæst_dato FROM snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def hent_dashboard_data() -> Dict:
    with _conn() as conn:
        snapshots = conn.execute(
            "SELECT id, rapport_dato FROM snapshots ORDER BY id ASC"
        ).fetchall()

        if not snapshots:
            return {
                "daglig_omsætning": [],
                "top_produkter": [],
                "kpi": {},
                "senest_opdateret": None,
            }

        # Daglige deltas: hvert snapshot er kumulativt fra startdato → rapport_dato
        daglig = []
        forrige_total = 0.0

        for snap in snapshots:
            total = conn.execute(
                "SELECT COALESCE(SUM(omsætning), 0) FROM produkter WHERE snapshot_id = ?",
                (snap["id"],)
            ).fetchone()[0]

            delta = max(0.0, total - forrige_total)
            daglig.append({"dato": snap["rapport_dato"], "omsætning": round(delta, 2)})
            forrige_total = total

        seneste_id = snapshots[-1]["id"]

        # Top 10 produkter fra seneste snapshot
        top = conn.execute("""
            SELECT varenavn,
                   SUM(omsætning) AS total_omsætning,
                   SUM(antal)     AS total_antal
            FROM   produkter
            WHERE  snapshot_id = ?
            GROUP  BY varenavn
            ORDER  BY total_omsætning DESC
            LIMIT  10
        """, (seneste_id,)).fetchall()

        # Totaler fra seneste snapshot til KPI
        totaler = conn.execute("""
            SELECT COALESCE(SUM(omsætning), 0) AS omsætning,
                   COALESCE(SUM(avance),    0) AS avance,
                   COUNT(*)                    AS antal_varer
            FROM   produkter
            WHERE  snapshot_id = ?
        """, (seneste_id,)).fetchone()

        avance_pct = 0.0
        if totaler["omsætning"] > 0:
            avance_pct = (totaler["avance"] / totaler["omsætning"]) * 100

        senest_opdateret = conn.execute(
            "SELECT indlæst_dato FROM snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()["indlæst_dato"]

    return {
        "daglig_omsætning": daglig,
        "top_produkter": [
            {
                "varenavn":  row["varenavn"] or "Ukendt",
                "omsætning": round(row["total_omsætning"], 2),
                "antal":     round(row["total_antal"], 1),
            }
            for row in top
        ],
        "kpi": {
            "seneste_dag_omsætning": daglig[-1]["omsætning"] if daglig else 0,
            "total_omsætning":       round(totaler["omsætning"], 2),
            "avance_pct":            round(avance_pct, 1),
            "antal_varer":           totaler["antal_varer"],
            "seneste_rapport_dato":  snapshots[-1]["rapport_dato"],
        },
        "senest_opdateret": senest_opdateret,
    }
