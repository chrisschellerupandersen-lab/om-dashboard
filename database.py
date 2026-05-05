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
                avance_pct  REAL    DEFAULT 0,
                time_start  INTEGER DEFAULT -1
            );

            CREATE INDEX IF NOT EXISTS idx_trans_dato ON transaktioner(dato);
            CREATE INDEX IF NOT EXISTS idx_trans_vare ON transaktioner(varenavn);
        """)
        # Migration: tilføj time_start til eksisterende tabeller
        try:
            conn.execute("ALTER TABLE transaktioner ADD COLUMN time_start INTEGER DEFAULT -1")
        except Exception:
            pass  # kolonnen eksisterer allerede


def gem_transaktioner(rapport_dato: str, transaktioner: List[Dict]) -> int:
    with _conn() as conn:
        conn.execute("DELETE FROM transaktioner")
        conn.execute("DELETE FROM uploads")

        cur = conn.execute(
            "INSERT INTO uploads (rapport_dato) VALUES (?)",
            (rapport_dato,)
        )
        upload_id = cur.lastrowid

        conn.executemany("""
            INSERT INTO transaktioner
                (dato, varenummer, varenavn, kategori, antal, omsætning, kostpris, avance, avance_pct, time_start)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                t.get("time_start", -1),
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


# ── NYE ENDPOINTS ─────────────────────────────────────────────────────────────

def hent_kpi() -> Dict:
    with _conn() as conn:
        seneste_dato = conn.execute(
            "SELECT MAX(dato) FROM transaktioner"
        ).fetchone()[0]

        if not seneste_dato:
            return {"dag": None, "uge": None, "snit_uge": None}

        dag = conn.execute("""
            SELECT COALESCE(SUM(omsætning),0)  AS omsaetning,
                   COUNT(*)                     AS transak,
                   COALESCE(SUM(avance),0)      AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(avance)/SUM(omsætning)*100
                        ELSE 0 END              AS db_pct
            FROM transaktioner WHERE dato = ?
        """, (seneste_dato,)).fetchone()

        seneste_yw = conn.execute(
            "SELECT strftime('%Y-%W', ?)", (seneste_dato,)
        ).fetchone()[0]

        uge = conn.execute("""
            SELECT COALESCE(SUM(omsætning),0)  AS omsaetning,
                   COALESCE(SUM(avance),0)      AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(avance)/SUM(omsætning)*100
                        ELSE 0 END              AS db_pct,
                   COUNT(DISTINCT dato)         AS antal_dage
            FROM transaktioner
            WHERE strftime('%Y-%W', dato) = ?
        """, (seneste_yw,)).fetchone()

        snit_row = conn.execute("""
            SELECT AVG(uge_total) AS snit_uge FROM (
                SELECT SUM(omsætning) AS uge_total
                FROM transaktioner
                GROUP BY strftime('%Y-%W', dato)
                ORDER BY dato DESC LIMIT 12
            )
        """).fetchone()

    return {
        "dag": dict(dag) if dag else None,
        "uge": dict(uge) if uge else None,
        "snit_uge": snit_row["snit_uge"] if snit_row else None,
    }


def hent_dage(n: int = 14) -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT dato, SUM(omsætning) AS omsaetning
            FROM transaktioner
            GROUP BY dato
            ORDER BY dato DESC
            LIMIT ?
        """, (n,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def hent_uger() -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT
                strftime('%Y', dato)  AS aar,
                CAST(strftime('%W', dato) AS INTEGER) AS uge,
                ROUND(SUM(omsætning), 2)              AS omsaetning,
                ROUND(SUM(avance), 2)                 AS db_kr,
                ROUND(CASE WHEN SUM(omsætning)>0
                     THEN SUM(avance)/SUM(omsætning)*100
                     ELSE 0 END, 1)                   AS db_pct,
                COUNT(DISTINCT dato)                  AS antal_dage
            FROM transaktioner
            GROUP BY strftime('%Y-%W', dato)
            ORDER BY dato ASC
        """).fetchall()
    return [dict(r) for r in rows]


def hent_timer_idag() -> List[Dict]:
    with _conn() as conn:
        seneste_dato = conn.execute(
            "SELECT MAX(dato) FROM transaktioner"
        ).fetchone()[0]
        if not seneste_dato:
            return []
        rows = conn.execute("""
            SELECT time_start, ROUND(SUM(omsætning), 2) AS omsaetning
            FROM transaktioner
            WHERE dato = ? AND time_start >= 0
            GROUP BY time_start
            ORDER BY time_start
        """, (seneste_dato,)).fetchall()
    return [dict(r) for r in rows]


def hent_timer_snit() -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT time_start, ugedag,
                   ROUND(AVG(dag_total), 2) AS snit_omsaetning
            FROM (
                SELECT
                    time_start,
                    dato,
                    CASE strftime('%w', dato)
                        WHEN '0' THEN 7
                        ELSE CAST(strftime('%w', dato) AS INTEGER)
                    END AS ugedag,
                    SUM(omsætning) AS dag_total
                FROM transaktioner
                WHERE time_start >= 0
                GROUP BY time_start, dato
            )
            GROUP BY time_start, ugedag
            ORDER BY time_start, ugedag
        """).fetchall()
    return [dict(r) for r in rows]


def hent_kategorier() -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT kategori, ROUND(SUM(omsætning), 2) AS omsaetning,
                   ROUND(SUM(avance)/NULLIF(SUM(omsætning),0)*100, 1) AS db_pct
            FROM transaktioner
            WHERE kategori != ''
            GROUP BY kategori
            ORDER BY omsaetning DESC
        """).fetchall()
    return [dict(r) for r in rows]


def hent_dage_detaljer(n: int = 8) -> List[Dict]:
    from datetime import datetime
    DAG_NAVNE    = ['Mandag','Tirsdag','Onsdag','Torsdag','Fredag','Lørdag','Søndag']
    MAANED_NAVNE = {1:'januar',2:'februar',3:'marts',4:'april',5:'maj',6:'juni',
                    7:'juli',8:'august',9:'september',10:'oktober',11:'november',12:'december'}

    with _conn() as conn:
        dage = conn.execute("""
            SELECT dato,
                   ROUND(SUM(omsætning), 2) AS omsaetning,
                   COUNT(*)                  AS linjer
            FROM transaktioner
            GROUP BY dato
            ORDER BY dato DESC
            LIMIT ?
        """, (n,)).fetchall()

        if not dage:
            return []

        dato_list    = [r['dato'] for r in dage]
        placeholders = ','.join('?' * len(dato_list))

        produkter = conn.execute(f"""
            SELECT dato, varenavn,
                   ROUND(SUM(antal), 0)    AS antal,
                   ROUND(SUM(omsætning), 2) AS omsaetning
            FROM transaktioner
            WHERE dato IN ({placeholders})
            GROUP BY dato, varenavn
            ORDER BY dato DESC, omsaetning DESC
        """, dato_list).fetchall()

    prod_by_dato: Dict[str, list] = {}
    for p in produkter:
        prod_by_dato.setdefault(p['dato'], []).append({
            'varenavn':  p['varenavn'],
            'antal':     int(p['antal']),
            'omsaetning': p['omsaetning'],
        })

    result = []
    for dag in dage:
        dato = dag['dato']
        d    = datetime.strptime(dato, '%Y-%m-%d')
        result.append({
            'dato':           dato,
            'dato_label':     f"{DAG_NAVNE[d.weekday()]} {d.day}. {MAANED_NAVNE[d.month]}",
            'omsaetning':     dag['omsaetning'],
            'linjer':         dag['linjer'],
            'snit_per_linje': round(dag['omsaetning'] / dag['linjer'], 0) if dag['linjer'] > 0 else 0,
            'produkter':      prod_by_dato.get(dato, [])[:15],
        })
    return result


def hent_top_produkter(n: int = 20) -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT varenavn,
                   ROUND(SUM(omsætning), 2)   AS omsaetning,
                   ROUND(SUM(antal), 0)        AS antal,
                   ROUND(SUM(avance)/NULLIF(SUM(omsætning),0)*100, 1) AS db_pct
            FROM transaktioner
            WHERE varenavn != ''
            GROUP BY varenavn
            ORDER BY omsaetning DESC
            LIMIT ?
        """, (n,)).fetchall()
    return [dict(r) for r in rows]


def hent_dashboard_data() -> Dict:
    with _conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM transaktioner").fetchone()[0]
        if count == 0:
            return {"daglig_omsætning": [], "top_produkter": [], "kpi": {}, "senest_opdateret": None}

        daglig = conn.execute("""
            SELECT dato, SUM(omsætning) AS omsætning
            FROM transaktioner GROUP BY dato ORDER BY dato ASC
        """).fetchall()

        top = conn.execute("""
            SELECT varenavn, SUM(omsætning) AS total_omsætning, SUM(antal) AS total_antal
            FROM transaktioner GROUP BY varenavn ORDER BY total_omsætning DESC LIMIT 10
        """).fetchall()

        seneste_dato = conn.execute("SELECT MAX(dato) FROM transaktioner").fetchone()[0]
        seneste_dag_omsætning = conn.execute(
            "SELECT COALESCE(SUM(omsætning), 0) FROM transaktioner WHERE dato = ?",
            (seneste_dato,)
        ).fetchone()[0]

        totaler = conn.execute("""
            SELECT COALESCE(SUM(omsætning),0) AS omsætning,
                   COALESCE(SUM(avance),0)    AS avance,
                   COUNT(DISTINCT varenavn)   AS antal_varer
            FROM transaktioner
        """).fetchone()

        avance_pct = 0.0
        if totaler["omsætning"] > 0:
            avance_pct = (totaler["avance"] / totaler["omsætning"]) * 100

        senest = conn.execute(
            "SELECT indlæst_dato FROM uploads ORDER BY id DESC LIMIT 1"
        ).fetchone()

    return {
        "daglig_omsætning": [{"dato": r["dato"], "omsætning": round(r["omsætning"], 2)} for r in daglig],
        "top_produkter": [{"varenavn": r["varenavn"] or "Ukendt", "omsætning": round(r["total_omsætning"], 2), "antal": round(r["total_antal"], 1)} for r in top],
        "kpi": {
            "seneste_dag_omsætning": round(seneste_dag_omsætning, 2),
            "total_omsætning":       round(totaler["omsætning"], 2),
            "avance_pct":            round(avance_pct, 1),
            "antal_varer":           totaler["antal_varer"],
            "seneste_rapport_dato":  seneste_dato,
        },
        "senest_opdateret": senest["indlæst_dato"] if senest else None,
    }
