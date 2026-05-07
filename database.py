import sqlite3
import os
from typing import List, Dict, Any, Optional

DB_PATH = os.environ.get("DB_PATH", "dashboard.db")

# Alle kaffedrikke — fanger kaffe, flat white, cappuccino, americano osv.
_KAFFE_WHERE = """(
    LOWER(varenavn) LIKE '%kaffe%'
    OR LOWER(varenavn) LIKE '%flat white%'
    OR LOWER(varenavn) LIKE '%cappuccino%'
    OR LOWER(varenavn) LIKE '%americano%'
    OR LOWER(varenavn) LIKE '%latte%'
    OR LOWER(varenavn) LIKE '%espresso%'
    OR LOWER(varenavn) LIKE '%macchiato%'
    OR LOWER(varenavn) LIKE '%cortado%'
    OR LOWER(varenavn) LIKE '%lungo%'
    OR LOWER(varenavn) LIKE '%mocha%'
)"""


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
                time_start  INTEGER DEFAULT -1,
                bon_nr      TEXT    DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_trans_dato ON transaktioner(dato);
            CREATE INDEX IF NOT EXISTS idx_trans_vare ON transaktioner(varenavn);

            CREATE TABLE IF NOT EXISTS ugebestillinger (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                uge           INTEGER NOT NULL,
                aar           INTEGER NOT NULL,
                varenummer    TEXT    DEFAULT '',
                varenavn      TEXT    NOT NULL,
                pris_ex_moms  REAL    DEFAULT 0,
                man           REAL    DEFAULT 0,
                tir           REAL    DEFAULT 0,
                ons           REAL    DEFAULT 0,
                tor           REAL    DEFAULT 0,
                fre           REAL    DEFAULT 0,
                loe           REAL    DEFAULT 0,
                son           REAL    DEFAULT 0,
                total_antal   REAL    DEFAULT 0,
                total_pris    REAL    DEFAULT 0,
                indlæst       TEXT    DEFAULT (datetime('now','localtime')),
                UNIQUE(uge, aar, varenavn) ON CONFLICT REPLACE
            );
            CREATE INDEX IF NOT EXISTS idx_bestil_uge ON ugebestillinger(uge, aar);

            CREATE TABLE IF NOT EXISTS bager_regnskab (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                uge           INTEGER NOT NULL,
                aar           INTEGER NOT NULL,
                retur_wiener  REAL DEFAULT 0,
                retur_boller  REAL DEFAULT 0,
                tgtg          REAL DEFAULT 0,
                b_kvali       REAL DEFAULT 0,
                retur_ialt    REAL DEFAULT 0,
                faktura       REAL DEFAULT 0,
                indlæst       TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(uge, aar) ON CONFLICT REPLACE
            );

            CREATE TABLE IF NOT EXISTS bestilling_manuel (
                uge        INTEGER NOT NULL,
                aar        INTEGER NOT NULL,
                varenummer TEXT    NOT NULL,
                dag        TEXT    NOT NULL,
                antal      INTEGER NOT NULL,
                PRIMARY KEY (uge, aar, varenummer, dag)
            );

            CREATE TABLE IF NOT EXISTS mobilepay (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                aar        INTEGER NOT NULL,
                maaned     INTEGER NOT NULL,
                omsaetning REAL    NOT NULL DEFAULT 0,
                UNIQUE(aar, maaned) ON CONFLICT REPLACE
            );
        """)
        # Migrationer til eksisterende tabeller
        for sql in [
            "ALTER TABLE transaktioner ADD COLUMN time_start INTEGER DEFAULT -1",
            "ALTER TABLE transaktioner ADD COLUMN bon_nr TEXT DEFAULT ''",
        ]:
            try:
                conn.execute(sql)
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
                (dato, varenummer, varenavn, kategori, antal, omsætning, kostpris, avance, avance_pct, time_start, bon_nr)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                t.get("bon_nr", ""),
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
                   COALESCE(SUM(kostpris),0)   AS vareforbrug,
                   CASE WHEN COUNT(CASE WHEN bon_nr != '' THEN 1 END) > 0
                        THEN COUNT(DISTINCT CASE WHEN bon_nr != '' THEN bon_nr END)
                        ELSE COUNT(*)
                   END                          AS transak,
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
                   COALESCE(SUM(kostpris),0)   AS vareforbrug,
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

        # Forrige uge med data (til uge-over-uge DB-sammenligning)
        prev_uge_row = conn.execute("""
            SELECT COALESCE(SUM(omsætning),0) AS omsaetning,
                   COALESCE(SUM(avance),0)    AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(avance)/SUM(omsætning)*100
                        ELSE 0 END            AS db_pct
            FROM transaktioner
            WHERE strftime('%Y-%W', dato) = (
                SELECT DISTINCT strftime('%Y-%W', dato)
                FROM transaktioner
                WHERE strftime('%Y-%W', dato) < ?
                ORDER BY dato DESC LIMIT 1
            )
        """, (seneste_yw,)).fetchone()

    return {
        "dag":      dict(dag)          if dag          else None,
        "uge":      dict(uge)          if uge          else None,
        "prev_uge": dict(prev_uge_row) if prev_uge_row else None,
        "snit_uge": snit_row["snit_uge"] if snit_row   else None,
    }


def hent_dag_produkter() -> Dict:
    """Produkter solgt seneste dag, sorteret efter omsætning."""
    with _conn() as conn:
        seneste_dato = conn.execute("SELECT MAX(dato) FROM transaktioner").fetchone()[0]
        if not seneste_dato:
            return {"dato": None, "produkter": []}
        rows = conn.execute("""
            SELECT varenavn,
                   ROUND(SUM(antal), 0)     AS antal,
                   ROUND(SUM(omsætning), 0) AS omsaetning,
                   ROUND(SUM(kostpris), 0)  AS vareforbrug,
                   ROUND(SUM(avance), 0)    AS db_kr,
                   ROUND(CASE WHEN SUM(omsætning)>0 THEN SUM(avance)/SUM(omsætning)*100 ELSE 0 END, 1) AS db_pct
            FROM transaktioner
            WHERE dato = ?
            GROUP BY varenavn
            ORDER BY omsaetning DESC
        """, (seneste_dato,)).fetchall()
    return {"dato": seneste_dato, "produkter": [dict(r) for r in rows]}


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
                ROUND(SUM(kostpris), 2)               AS vareforbrug,
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
            'produkter':      prod_by_dato.get(dato, []),
        })
    return result


def hent_aarsdata(aar: int = None) -> Dict:
    from datetime import datetime
    if aar is None:
        aar = datetime.now().year
    with _conn() as conn:
        rows = conn.execute("""
            SELECT
                CAST(strftime('%m', dato) AS INTEGER) AS maaned,
                COUNT(DISTINCT dato)                   AS faktiske_dage,
                ROUND(SUM(omsætning), 2)               AS omsaetning,
                ROUND(SUM(kostpris),  2)               AS kostpris,
                ROUND(SUM(avance),    2)               AS avance,
                ROUND(SUM(avance)/NULLIF(SUM(omsætning),0)*100, 1) AS gpm
            FROM transaktioner
            WHERE strftime('%Y', dato) = ?
            GROUP BY maaned
            ORDER BY maaned
        """, (str(aar),)).fetchall()

        prev_dec = conn.execute("""
            SELECT COUNT(DISTINCT dato) AS faktiske_dage,
                   ROUND(SUM(omsætning), 2) AS omsaetning,
                   ROUND(SUM(kostpris),  2) AS kostpris,
                   ROUND(SUM(avance),    2) AS avance,
                   ROUND(SUM(avance)/NULLIF(SUM(omsætning),0)*100, 1) AS gpm
            FROM transaktioner WHERE strftime('%Y-%m', dato) = ?
        """, (f"{aar-1}-12",)).fetchone()

        seneste = conn.execute("SELECT MAX(dato) FROM transaktioner").fetchone()[0]
        base_row = None
        if seneste:
            base_row = conn.execute("""
                SELECT
                    ROUND(SUM(omsætning)/NULLIF(COUNT(DISTINCT dato),0), 2) AS kr_pr_dag,
                    ROUND(SUM(avance)/NULLIF(SUM(omsætning),0)*100, 1)      AS gpm
                FROM transaktioner
                WHERE dato >= date(?, '-28 days')
            """, (seneste,)).fetchone()

    return {
        "aar":            aar,
        "maaneder":       [dict(r) for r in rows],
        "prev_dec":       dict(prev_dec) if prev_dec and prev_dec["omsaetning"] else None,
        "base_kr_pr_dag": base_row["kr_pr_dag"] if base_row else None,
        "base_gpm":       base_row["gpm"]       if base_row else None,
    }


def hent_trend_analyse(periode_dage: int = 21) -> Dict:
    """Sammenlign seneste periode mod forrige periode (dagsnormaliseret)."""
    from datetime import datetime, timedelta
    with _conn() as conn:
        seneste_dato = conn.execute("SELECT MAX(dato) FROM transaktioner").fetchone()[0]
        tidligste_dato = conn.execute("SELECT MIN(dato) FROM transaktioner").fetchone()[0]
        if not seneste_dato:
            return {}

        slut  = datetime.strptime(seneste_dato, '%Y-%m-%d')
        midt  = slut  - timedelta(days=periode_dage)
        start = midt  - timedelta(days=periode_dage)
        midt_str  = midt.strftime('%Y-%m-%d')
        start_str = start.strftime('%Y-%m-%d')

        rows = conn.execute("""
            SELECT
                varenavn, kategori,
                ROUND(SUM(CASE WHEN dato > ? THEN antal      ELSE 0 END), 1) AS ny_antal,
                ROUND(SUM(CASE WHEN dato > ? THEN omsætning  ELSE 0 END), 2) AS ny_omsat,
                ROUND(SUM(CASE WHEN dato > ? AND dato <= ? THEN antal      ELSE 0 END), 1) AS gl_antal,
                ROUND(SUM(CASE WHEN dato > ? AND dato <= ? THEN omsætning  ELSE 0 END), 2) AS gl_omsat,
                ROUND(SUM(avance)/NULLIF(SUM(omsætning),0)*100, 1) AS db_pct
            FROM transaktioner
            WHERE dato > ? AND varenavn != ''
            GROUP BY varenavn
            HAVING ny_omsat > 0 OR gl_omsat > 0
        """, (midt_str, midt_str,
              start_str, midt_str,
              start_str, midt_str,
              start_str)).fetchall()

        ny_dage = conn.execute(
            "SELECT COUNT(DISTINCT dato) FROM transaktioner WHERE dato > ?", (midt_str,)
        ).fetchone()[0] or 1
        gl_dage = conn.execute(
            "SELECT COUNT(DISTINCT dato) FROM transaktioner WHERE dato > ? AND dato <= ?",
            (start_str, midt_str)
        ).fetchone()[0] or 1

        ny_total = conn.execute(
            "SELECT COALESCE(SUM(omsætning),0) FROM transaktioner WHERE dato > ?", (midt_str,)
        ).fetchone()[0]
        gl_total = conn.execute(
            "SELECT COALESCE(SUM(omsætning),0) FROM transaktioner WHERE dato > ? AND dato <= ?",
            (start_str, midt_str)
        ).fetchone()[0]

    return {
        "perioder": {
            "ny_fra":  midt_str,   "ny_til":  seneste_dato,
            "gl_fra":  start_str,  "gl_til":  midt_str,
            "ny_dage": ny_dage,    "gl_dage": gl_dage,
            "data_fra": tidligste_dato,
        },
        "ny_total": ny_total,
        "gl_total": gl_total,
        "produkter": [dict(r) for r in rows],
    }


def hent_kaffe_analyse() -> Dict:
    with _conn() as conn:
        kpi = conn.execute(f"""
            SELECT
                ROUND(SUM(antal), 0)                                      AS total_antal,
                ROUND(SUM(omsætning), 2)                                  AS total_omsaetning,
                ROUND(SUM(avance), 2)                                     AS total_avance,
                ROUND(SUM(avance)/NULLIF(SUM(omsætning),0)*100, 1)       AS db_pct,
                ROUND(SUM(omsætning)/NULLIF(SUM(antal),0), 2)            AS gns_pris
            FROM transaktioner
            WHERE {_KAFFE_WHERE}
        """).fetchone()

        total_omsat = conn.execute(
            "SELECT COALESCE(SUM(omsætning),0) FROM transaktioner"
        ).fetchone()[0]

        produkter = conn.execute(f"""
            SELECT varenavn,
                   ROUND(SUM(antal), 0)                                   AS antal,
                   ROUND(SUM(omsætning), 2)                               AS omsaetning,
                   ROUND(SUM(avance)/NULLIF(SUM(omsætning),0)*100, 1)    AS db_pct
            FROM transaktioner
            WHERE {_KAFFE_WHERE}
            GROUP BY varenavn
            ORDER BY omsaetning DESC
        """).fetchall()

        dage_rows = conn.execute(f"""
            SELECT dato,
                   ROUND(SUM(antal), 0)    AS antal,
                   ROUND(SUM(omsætning), 2) AS omsaetning
            FROM transaktioner
            WHERE {_KAFFE_WHERE}
            GROUP BY dato
            ORDER BY dato DESC
            LIMIT 30
        """).fetchall()

        timer = conn.execute(f"""
            SELECT time_start,
                   ROUND(SUM(antal), 0)      AS total_antal,
                   ROUND(SUM(omsætning), 2)  AS total_omsaetning,
                   ROUND(SUM(antal) * 100.0 / NULLIF(SUM(SUM(antal)) OVER (), 0), 1) AS pct
            FROM transaktioner
            WHERE {_KAFFE_WHERE} AND time_start >= 0
            GROUP BY time_start
            ORDER BY time_start
        """).fetchall()

        timer_produkter = conn.execute(f"""
            SELECT time_start, varenavn,
                   ROUND(SUM(antal), 0) AS total_antal
            FROM transaktioner
            WHERE {_KAFFE_WHERE} AND time_start >= 0
            GROUP BY time_start, varenavn
            ORDER BY time_start, total_antal DESC
        """).fetchall()

        dage_produkter = conn.execute(f"""
            SELECT dato, varenavn,
                   ROUND(SUM(antal), 0) AS total_antal
            FROM transaktioner
            WHERE {_KAFFE_WHERE}
            GROUP BY dato, varenavn
            ORDER BY dato DESC, total_antal DESC
        """).fetchall()

    return {
        "kpi":              dict(kpi) if kpi else {},
        "total_omsat":      total_omsat,
        "produkter":        [dict(r) for r in produkter],
        "dage":             [dict(r) for r in reversed(list(dage_rows))],
        "timer":            [dict(r) for r in timer],
        "timer_produkter":  [dict(r) for r in timer_produkter],
        "dage_produkter":   [dict(r) for r in dage_produkter],
    }


def hent_top_produkter(n: int = 20) -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT varenavn,
                   ROUND(SUM(omsætning), 2)                            AS omsaetning,
                   ROUND(SUM(kostpris), 2)                             AS vareforbrug,
                   ROUND(SUM(antal), 0)                                AS antal,
                   ROUND(SUM(avance), 2)                               AS db_kr,
                   ROUND(SUM(avance)/NULLIF(SUM(omsætning),0)*100, 1)  AS db_pct
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


def gem_ugebestilling(uge: int, aar: int, linjer: List[Dict]) -> int:
    with _conn() as conn:
        for linje in linjer:
            conn.execute("""
                INSERT INTO ugebestillinger
                    (uge, aar, varenummer, varenavn, pris_ex_moms,
                     man, tir, ons, tor, fre, loe, son, total_antal, total_pris)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                uge, aar,
                linje.get("varenummer", ""),
                linje["varenavn"],
                linje.get("pris_ex_moms", 0),
                linje.get("man", 0), linje.get("tir", 0), linje.get("ons", 0),
                linje.get("tor", 0), linje.get("fre", 0), linje.get("loe", 0),
                linje.get("son", 0),
                linje.get("total_antal", 0),
                linje.get("total_pris", 0),
            ))
    return len(linjer)


def hent_bestilling_uger() -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT uge, aar,
                   COUNT(*)                    AS antal_varer,
                   ROUND(SUM(total_antal), 0)  AS total_antal,
                   ROUND(SUM(total_pris), 2)   AS total_pris,
                   MAX(indlæst)                AS indlæst
            FROM ugebestillinger
            GROUP BY uge, aar
            ORDER BY aar DESC, uge DESC
        """).fetchall()
    return [dict(r) for r in rows]


def hent_bestilling_uge(uge: int, aar: int) -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT varenummer, varenavn, pris_ex_moms,
                   man, tir, ons, tor, fre, loe, son,
                   total_antal, total_pris
            FROM ugebestillinger
            WHERE uge = ? AND aar = ?
            ORDER BY total_pris DESC
        """, (uge, aar)).fetchall()
    return [dict(r) for r in rows]


def gem_bager_regnskab(linjer: List[Dict]) -> int:
    with _conn() as conn:
        for r in linjer:
            conn.execute("""
                INSERT INTO bager_regnskab
                    (uge, aar, retur_wiener, retur_boller, tgtg, b_kvali, retur_ialt, faktura)
                VALUES (?,?,?,?,?,?,?,?)
            """, (r["uge"], r["aar"], r.get("retur_wiener", 0), r.get("retur_boller", 0),
                  r.get("tgtg", 0), r.get("b_kvali", 0), r.get("retur_ialt", 0), r.get("faktura", 0)))
    return len(linjer)


def hent_svind_data() -> List[Dict]:
    """Kombinerer bestilling, bager_regnskab og kassesalg per uge.
    Effektivt solgt = kassesalg_stk + KW-kombostk + TGTG_stk (tgtg_kr ÷ 38 kr/pose).
    """
    TGTG_KR_PR_POSE = 38.0

    from datetime import date as _date

    with _conn() as conn:
        # Kassesalg bagværk per dag — matcher varenummer fra bestillinger
        # Aggregeres til ISO-uger i Python (SQLite %W ≠ ISO-ugenummer)
        kasse_dage = conn.execute("""
            SELECT dato, ROUND(SUM(antal), 0) AS kassesalg_stk
            FROM transaktioner
            WHERE CAST(CAST(varenummer AS REAL) AS INTEGER) IN (
                SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                FROM ugebestillinger
                WHERE varenummer != '' AND varenummer != '0'
            )
            GROUP BY dato
        """).fetchall()
        kasse_map: Dict = {}
        for r in kasse_dage:
            iso = _date.fromisoformat(r["dato"]).isocalendar()
            key = (iso[1], iso[0])
            kasse_map[key] = kasse_map.get(key, 0) + (r["kassesalg_stk"] or 0)

        # KW stk: Kaffe+Wienerbrød-kombination — per dag → ISO-uge
        kw_dage = conn.execute("""
            SELECT dato, ROUND(SUM(antal), 0) AS kw_stk
            FROM transaktioner
            WHERE (LOWER(varenavn) LIKE '%kaffe%' AND LOWER(varenavn) LIKE '%wiener%')
               OR (LOWER(varenavn) LIKE '%kaffe%' AND LOWER(varenavn) LIKE '%bmo%')
            GROUP BY dato
        """).fetchall()
        kw_map: Dict = {}
        for r in kw_dage:
            iso = _date.fromisoformat(r["dato"]).isocalendar()
            key = (iso[1], iso[0])
            kw_map[key] = kw_map.get(key, 0) + (r["kw_stk"] or 0)

        rows = conn.execute("""
            SELECT
                b.uge, b.aar,
                ROUND(SUM(u.total_antal), 0)                   AS bestilt_stk,
                ROUND(SUM(u.total_pris),  2)                   AS bestilt_kr,
                b.retur_wiener, b.retur_boller, b.tgtg, b.b_kvali, b.retur_ialt,
                ROUND(SUM(u.total_pris) - b.retur_ialt, 2)    AS netto_kr
            FROM bager_regnskab b
            LEFT JOIN ugebestillinger u ON u.uge = b.uge AND u.aar = b.aar
            GROUP BY b.uge, b.aar
            UNION ALL
            -- Uger med bestilling men uden bager_regnskab endnu
            SELECT
                u.uge, u.aar,
                ROUND(SUM(u.total_antal), 0) AS bestilt_stk,
                ROUND(SUM(u.total_pris),  2) AS bestilt_kr,
                0, 0, 0, 0, 0,
                ROUND(SUM(u.total_pris), 2)  AS netto_kr
            FROM ugebestillinger u
            WHERE NOT EXISTS (
                SELECT 1 FROM bager_regnskab b WHERE b.uge = u.uge AND b.aar = u.aar
            )
            GROUP BY u.uge, u.aar
            ORDER BY aar DESC, uge DESC
        """).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        kassesalg = kasse_map.get((d["uge"], d["aar"]))
        kw_stk    = int(kw_map.get((d["uge"], d["aar"]), 0) or 0)
        tgtg_stk  = round(d["tgtg"] / TGTG_KR_PR_POSE) if d.get("tgtg") else 0

        d["kassesalg_stk"] = kassesalg
        d["kw_stk"]        = kw_stk
        d["tgtg_stk"]      = tgtg_stk

        if kassesalg is not None and d["bestilt_stk"]:
            effektivt = kassesalg + kw_stk + tgtg_stk
            svind     = d["bestilt_stk"] - effektivt
            d["effektivt_solgt"] = effektivt
            d["svind_stk"]  = svind
            d["svind_pct"]  = round(svind / d["bestilt_stk"] * 100, 1)
        else:
            d["effektivt_solgt"] = None
            d["svind_stk"]  = None
            d["svind_pct"]  = None
        result.append(d)
    return result


def hent_mangler_kostpris() -> Dict:
    """Produkter hvor total kostpris = 0 på tværs af alle transaktioner."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT
                varenavn,
                kategori,
                ROUND(SUM(antal), 0)      AS total_antal,
                ROUND(SUM(omsætning), 2)  AS total_omsaetning,
                MAX(dato)                 AS seneste_dato,
                COUNT(DISTINCT dato)      AS salgs_dage
            FROM transaktioner
            WHERE varenavn != ''
            GROUP BY varenavn, kategori
            HAVING SUM(kostpris) = 0 AND SUM(omsætning) > 0
            ORDER BY total_omsaetning DESC
        """).fetchall()

        total_omsat = conn.execute(
            "SELECT COALESCE(SUM(omsætning),0) FROM transaktioner"
        ).fetchone()[0]

    produkter = [dict(r) for r in rows]
    mangler_omsat = sum(p["total_omsaetning"] for p in produkter)
    return {
        "produkter": produkter,
        "antal":     len(produkter),
        "mangler_omsaetning": round(mangler_omsat, 2),
        "total_omsaetning":   round(total_omsat, 2),
    }


# ── BESTILLINGSBEREGNER ───────────────────────────────────────────────────────

_SI_MAANED = {1:.88, 2:.83, 3:.87, 4:1.10, 5:1.12, 6:1.15,
              7:1.08, 8:1.10, 9:1.00, 10:.97, 11:.95, 12:1.85}

_EVENTS: Dict = {
    (7,  2026): {"factor": 1.20, "navn": "Fastelavn",
                 "note": "Mere wienerbrød og boller — bestil fastelavnsboller",
                 "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.0,"loe":1.3,"son":1.0}},
    (14, 2026): {"factor": 1.15, "navn": "Påskeuge (2.–5. apr.)",
                 "note": "Lang weekend — ekstra på torsdag og fredag",
                 "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.3,"fre":1.4,"loe":1.2,"son":1.0}},
    (15, 2026): {"factor": 0.90, "navn": "Påske — mandag lukket",
                 "note": "Reducer mandag-leverancen (2. påskedag)",
                 "dag_fak": {"man":0.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.0,"loe":1.0,"son":1.0}},
    (18, 2026): {"factor": 1.10, "navn": "Store Bededag (1. maj)",
                 "note": "+10% — fridag i ugen",
                 "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.2,"loe":1.2,"son":1.0}},
    (20, 2026): {"factor": 1.15, "navn": "Kr. Himmelfart + brofridag",
                 "note": "Fredag 15. maj er brofridag — bestil 45% ekstra fredag",
                 "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":0.5,"fre":1.45,"loe":1.2,"son":1.0}},
    (21, 2026): {"factor": 1.15, "navn": "Pinse (søn. 24. maj)",
                 "note": "Søndagsleverance dækker søndag + mandag",
                 "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.1,"loe":1.2,"son":1.4}},
    (22, 2026): {"factor": 0.88, "navn": "2. Pinsedag — mandag lukket",
                 "note": "Reducer første leverance mandag",
                 "dag_fak": {"man":0.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.0,"loe":1.0,"son":1.0}},
    (23, 2026): {"factor": 1.25, "navn": "Grundlovsdag (fre. 5. jun.)",
                 "note": "Fredag er årets bedste bagværksdag — bestil 60% ekstra fredag",
                 "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.60,"loe":1.2,"son":1.0}},
    (52, 2026): {"factor": 1.85, "navn": "Juleugen",
                 "note": "Årets travleste uge — planlæg indkøb i oktober",
                 "dag_fak": {"man":1.2,"tir":1.3,"ons":1.4,"tor":1.5,"fre":1.6,"loe":1.4,"son":1.0}},
    (1,  2027): {"factor": 0.45, "navn": "Nytårsuge",
                 "note": "Halv bestilling — butik lukket/kort uge",
                 "dag_fak": {"man":0.0,"tir":0.5,"ons":0.5,"tor":0.5,"fre":0.5,"loe":0.5,"son":0.0}},
}

_RB = 0.10    # returrate boller (10% sendes retur)
_RW = 0.135   # returrate wienerbrød (13.5%)
_BUFFER = 1.05


def _kat(varenavn: str) -> str:
    n = (varenavn or '').lower()
    if 'rugbrød' in n or 'rugbrod' in n:
        return 'Rugbrød'
    if 'flute' in n or 'flûte' in n:
        return 'Flute'
    if 'bolle' in n:
        return 'Boller'
    # Studenterbrød er kage trods "brød" i navnet
    if ('brød' in n or 'brod' in n) and 'wiener' not in n and 'studenter' not in n:
        return 'Brød'
    if 'wiener' in n or 'spandauer' in n:
        return 'Wiener'
    # Alt andet (croissant, brownie, cookies, træstammer, romkugler osv.) → Kage
    return 'Kage'


def _dato_range(iso_uge: int, aar: int) -> str:
    from datetime import date, timedelta
    MND = ['', 'jan.', 'feb.', 'mar.', 'apr.', 'maj', 'jun.',
           'jul.', 'aug.', 'sep.', 'okt.', 'nov.', 'dec.']
    jan4 = date(aar, 1, 4)
    w1_mon = jan4 - timedelta(days=jan4.weekday())
    mon = w1_mon + timedelta(weeks=iso_uge - 1)
    sun = mon + timedelta(days=6)
    if mon.month == sun.month:
        return f"{mon.day}.–{sun.day}. {MND[mon.month]}"
    return f"{mon.day}. {MND[mon.month]}–{sun.day}. {MND[sun.month]}"


def hent_bestillings_anbefaling() -> Dict:
    """Anbefalede bestillinger for næste 5 uger.
    Formel: basis × buffer × SI × begivenhedsfaktor × TGTG-korrektion × vækstfaktor
    """
    from datetime import date, timedelta
    TGTG_PR_POSE = 38.0

    today = date.today()
    t_iso = today.isocalendar()

    with _conn() as conn:
        # Kategorifordeling fra seneste 4 ugers bestillinger
        kat_rows = conn.execute("""
            WITH top4 AS (
                SELECT DISTINCT uge, aar FROM ugebestillinger
                ORDER BY aar DESC, uge DESC LIMIT 4
            )
            SELECT varenavn, SUM(total_antal) AS stk
            FROM ugebestillinger JOIN top4 USING (uge, aar)
            GROUP BY varenavn
        """).fetchall()

        kat_sum = {"Boller": 0.0, "Wiener": 0.0, "Brød": 0.0,
                   "Kage": 0.0, "Rugbrød": 0.0, "Flute": 0.0}
        for r in kat_rows:
            kat_sum[_kat(r["varenavn"])] += (r["stk"] or 0)
        grand = sum(kat_sum.values()) or 1
        kat_pct = {k: v / grand for k, v in kat_sum.items()}

        # Effektivt solgt seneste 8 uger (kassesalg + KW + TGTG)
        salg_rows = conn.execute("""
            WITH kasse AS (
                SELECT CAST(CAST(strftime('%W',dato) AS INTEGER) AS TEXT) AS uw,
                       strftime('%Y',dato) AS uy,
                       ROUND(SUM(antal),0) AS stk
                FROM transaktioner
                WHERE CAST(CAST(varenummer AS REAL) AS INTEGER) IN (
                    SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                    FROM ugebestillinger WHERE varenummer!='' AND varenummer!='0'
                )
                GROUP BY uw, uy
            ),
            kw AS (
                SELECT CAST(CAST(strftime('%W',dato) AS INTEGER) AS TEXT) AS uw,
                       strftime('%Y',dato) AS uy,
                       ROUND(SUM(antal),0) AS stk
                FROM transaktioner
                WHERE (LOWER(varenavn) LIKE '%kaffe%' AND LOWER(varenavn) LIKE '%wiener%')
                   OR (LOWER(varenavn) LIKE '%kaffe%' AND LOWER(varenavn) LIKE '%bmo%')
                GROUP BY uw, uy
            )
            SELECT CAST(k.uw AS INTEGER) AS uge,
                   CAST(k.uy AS INTEGER) AS aar,
                   k.stk + COALESCE(kw.stk,0) AS kasse_stk,
                   br.tgtg AS tgtg_kr
            FROM kasse k
            LEFT JOIN kw ON kw.uw=k.uw AND kw.uy=k.uy
            LEFT JOIN bager_regnskab br
                ON br.uge=CAST(k.uw AS INTEGER) AND br.aar=CAST(k.uy AS INTEGER)
            ORDER BY aar DESC, uge DESC
            LIMIT 8
        """).fetchall()

    # Beregn effektivt solgt pr. uge
    eff = []
    for r in salg_rows:
        tgtg_stk = round((r["tgtg_kr"] or 0) / TGTG_PR_POSE)
        eff.append({
            "uge": r["uge"], "aar": r["aar"],
            "v": (r["kasse_stk"] or 0) + tgtg_stk,
            "tgtg_kr": r["tgtg_kr"] or 0,
        })

    # Basis: snit af seneste 3 uger med data
    basis3 = [e["v"] for e in eff[:3] if e["v"] > 0]
    basis  = sum(basis3) / len(basis3) if basis3 else 1000.0

    # Vækst: seneste 3 vs. forrige 3 (cap ±15%)
    prev3 = [e["v"] for e in eff[3:6] if e["v"] > 0]
    prev  = sum(prev3) / len(prev3) if prev3 else basis
    vaekst = max(-0.15, min(0.15, basis / prev - 1)) if prev > 0 else 0.0

    # TGTG-korrektion baseret på seneste tilgængelige uge
    tgtg_kr = next((e["tgtg_kr"] for e in eff if e["tgtg_kr"] > 0), 0)
    tgtg_korr = 0.95 if tgtg_kr > 1000 else 1.0

    # Beregn anbefaling for næste 5 uger
    uger_list = []
    for i in range(1, 6):
        tgt = today + timedelta(weeks=i)
        u_iso = tgt.isocalendar()
        u_uge, u_aar = u_iso[1], u_iso[0]
        mon_dato = date.fromisocalendar(u_aar, u_uge, 1)
        u_mdr = mon_dato.month

        si   = _SI_MAANED.get(u_mdr, 1.0)
        evt  = _EVENTS.get((u_uge, u_aar))
        efak = evt["factor"] if evt else 1.0
        tot_fak = si * efak * tgtg_korr * (1 + vaekst)

        netto = round(basis * _BUFFER * tot_fak)

        kats: Dict = {}
        for kat, pct in kat_pct.items():
            n = round(netto * pct)
            if kat == "Boller":  r_stk = round(n * _RB / (1 - _RB))
            elif kat == "Wiener": r_stk = round(n * _RW / (1 - _RW))
            else:                 r_stk = 0
            kats[kat] = {"netto": n, "retur": r_stk, "brutto": n + r_stk}

        brutto_total = sum(v["brutto"] for v in kats.values())

        uger_list.append({
            "uge":            u_uge,
            "aar":            u_aar,
            "dato_range":     _dato_range(u_uge, u_aar),
            "maaned":         u_mdr,
            "si":             round(si, 2),
            "event":          evt,
            "tgtg_korrektion": round(tgtg_korr, 2),
            "vaekst_pct":     round(vaekst * 100, 1),
            "total_faktor":   round(tot_fak, 3),
            "netto_stk":      netto,
            "brutto_stk":     brutto_total,
            "kategorier":     kats,
        })

    return {
        "basis_snit":   round(basis),
        "basis_uger":   len(basis3),
        "vaekst_pct":   round(vaekst * 100, 1),
        "tgtg_kr":      round(tgtg_kr),
        "tgtg_ok":      tgtg_kr < 800,
        "tgtg_advarsel": tgtg_kr > 1200,
        "tgtg_korrektion": round(tgtg_korr, 2),
        "kat_fordeling": {k: round(v * 100, 1) for k, v in kat_pct.items()},
        "uger":          uger_list,
    }


def gem_bestilling_manuel(uge: int, aar: int, varenummer: str, dag: str, antal: int):
    with _conn() as conn:
        conn.execute("""
            INSERT INTO bestilling_manuel (uge, aar, varenummer, dag, antal)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(uge, aar, varenummer, dag) DO UPDATE SET antal=excluded.antal
        """, (uge, aar, varenummer, dag, antal))


def gem_mobilepay(aar: int, maaned: int, omsaetning: float):
    with _conn() as conn:
        conn.execute("""
            INSERT INTO mobilepay (aar, maaned, omsaetning)
            VALUES (?, ?, ?)
            ON CONFLICT(aar, maaned) DO UPDATE SET omsaetning=excluded.omsaetning
        """, (aar, maaned, omsaetning))


def hent_mobilepay() -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT aar, maaned, omsaetning
            FROM mobilepay
            ORDER BY aar DESC, maaned DESC
        """).fetchall()
    return [dict(r) for r in rows]


def hent_bestillings_uge(maal_uge: int, maal_aar: int) -> Dict:
    """Produktniveau bestillingsanbefaling for mål-uge.

    Basis: senest indlæste ugebestilling før mål-ugen.
    Formel pr. dag: basis_dag × SI × dag_fak × TGTG-korr × (1 + vækst)
    """
    from datetime import date, timedelta
    DAGE = ['man', 'tir', 'ons', 'tor', 'fre', 'loe', 'son']
    TGTG_PR_POSE = 38.0

    with _conn() as conn:
        # Find seneste bestillingsuge der er ældre end (eller lig) mål-ugen
        basis_row = conn.execute("""
            SELECT uge, aar FROM ugebestillinger
            WHERE (aar < ? OR (aar = ? AND uge < ?))
            ORDER BY aar DESC, uge DESC
            LIMIT 1
        """, (maal_aar, maal_aar, maal_uge)).fetchone()

        if not basis_row:
            # Fallback: seneste uge overhovedet
            basis_row = conn.execute("""
                SELECT uge, aar FROM ugebestillinger
                ORDER BY aar DESC, uge DESC LIMIT 1
            """).fetchone()

        if not basis_row:
            return {"fejl": "Ingen ugebestillinger indlæst endnu"}

        basis_uge = basis_row["uge"]
        basis_aar = basis_row["aar"]

        # Hent alle produkter fra basis-ugen — bevar original rækkefølge (id)
        prod_rows = conn.execute("""
            SELECT varenummer, varenavn, pris_ex_moms,
                   man, tir, ons, tor, fre, loe, son, total_antal
            FROM ugebestillinger
            WHERE uge=? AND aar=?
            ORDER BY id
        """, (basis_uge, basis_aar)).fetchall()

        # Manuelle overrides for mål-ugen
        manuel_rows = conn.execute("""
            SELECT varenummer, dag, antal FROM bestilling_manuel
            WHERE uge=? AND aar=?
        """, (maal_uge, maal_aar)).fetchall()
        manuel: Dict = {}
        for mr in manuel_rows:
            if mr["varenummer"] not in manuel:
                manuel[mr["varenummer"]] = {}
            manuel[mr["varenummer"]][mr["dag"]] = mr["antal"]

        # Effektivt solgt seneste 8 uger til vækst+TGTG
        salg_rows = conn.execute("""
            WITH kasse AS (
                SELECT CAST(CAST(strftime('%W',dato) AS INTEGER) AS TEXT) AS uw,
                       strftime('%Y',dato) AS uy,
                       ROUND(SUM(antal),0) AS stk
                FROM transaktioner
                WHERE CAST(CAST(varenummer AS REAL) AS INTEGER) IN (
                    SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                    FROM ugebestillinger WHERE varenummer!='' AND varenummer!='0'
                )
                GROUP BY uw, uy
            ),
            kw AS (
                SELECT CAST(CAST(strftime('%W',dato) AS INTEGER) AS TEXT) AS uw,
                       strftime('%Y',dato) AS uy,
                       ROUND(SUM(antal),0) AS stk
                FROM transaktioner
                WHERE (LOWER(varenavn) LIKE '%kaffe%' AND LOWER(varenavn) LIKE '%wiener%')
                   OR (LOWER(varenavn) LIKE '%kaffe%' AND LOWER(varenavn) LIKE '%bmo%')
                GROUP BY uw, uy
            )
            SELECT CAST(k.uw AS INTEGER) AS uge,
                   CAST(k.uy AS INTEGER) AS aar,
                   k.stk + COALESCE(kw.stk,0) AS kasse_stk,
                   br.tgtg AS tgtg_kr
            FROM kasse k
            LEFT JOIN kw ON kw.uw=k.uw AND kw.uy=k.uy
            LEFT JOIN bager_regnskab br
                ON br.uge=CAST(k.uw AS INTEGER) AND br.aar=CAST(k.uy AS INTEGER)
            ORDER BY aar DESC, uge DESC
            LIMIT 8
        """).fetchall()

    # Vækst: seneste 3 vs forrige 3 uger, cap ±15%
    eff = [(r["kasse_stk"] or 0) + round((r["tgtg_kr"] or 0) / TGTG_PR_POSE)
           for r in salg_rows]
    basis3 = [v for v in eff[:3] if v > 0]
    prev3  = [v for v in eff[3:6] if v > 0]
    basis_avg = sum(basis3) / len(basis3) if basis3 else 1.0
    prev_avg  = sum(prev3)  / len(prev3)  if prev3  else basis_avg
    vaekst = max(-0.15, min(0.15, basis_avg / prev_avg - 1)) if prev_avg > 0 else 0.0

    # TGTG-korrektion
    tgtg_kr = next((r["tgtg_kr"] for r in salg_rows if (r["tgtg_kr"] or 0) > 0), 0) or 0
    tgtg_korr = 0.95 if tgtg_kr > 1000 else 1.0

    # Sæsonindeks for mål-ugens mandag
    mon_dato = date.fromisocalendar(maal_aar, maal_uge, 1)
    si = _SI_MAANED.get(mon_dato.month, 1.0)

    # Event / helligdage
    evt = _EVENTS.get((maal_uge, maal_aar))
    dag_fak = evt["dag_fak"] if evt else {d: 1.0 for d in DAGE}
    total_faktor = si * (evt["factor"] if evt else 1.0) * tgtg_korr * (1 + vaekst)

    # Byg produkttabel
    produkter = []
    for r in prod_rows:
        basis_dag = {d: float(r[d] or 0) for d in DAGE}
        kat = _kat(r["varenavn"])
        vn  = r["varenummer"] or ""

        if kat == 'Kage':
            anb_dag = {d: int(basis_dag[d]) for d in DAGE}
        else:
            anb_dag = {}
            for d in DAGE:
                raw = basis_dag[d] * si * dag_fak.get(d, 1.0) * tgtg_korr * (1 + vaekst)
                anb_dag[d] = int(round(raw))

        # Anvend manuelle overrides
        vn_manuel = manuel.get(vn, {})
        for d in DAGE:
            if d in vn_manuel:
                anb_dag[d] = vn_manuel[d]

        total_basis = sum(basis_dag[d] for d in DAGE)
        total_anb   = sum(anb_dag[d]   for d in DAGE)
        pris = float(r["pris_ex_moms"] or 0)

        produkter.append({
            "varenummer":      vn,
            "varenavn":        r["varenavn"],
            "kategori":        kat,
            "pris_ex_moms":    round(pris, 2),
            "basis":           {d: int(basis_dag[d]) for d in DAGE},
            "anbefalet":       anb_dag,
            "manuel":          {d: True for d in DAGE if d in vn_manuel},
            "total_basis":     int(total_basis),
            "total_anbefalet": total_anb,
            "total_pris":      round(total_anb * pris, 2),
        })

    total_stk = sum(p["total_anbefalet"] for p in produkter)
    total_kr  = sum(p["total_pris"]      for p in produkter)

    return {
        "maal_uge":        maal_uge,
        "maal_aar":        maal_aar,
        "dato_range":      _dato_range(maal_uge, maal_aar),
        "basis_uge":       basis_uge,
        "basis_aar":       basis_aar,
        "maaned":          mon_dato.month,
        "si":              round(si, 2),
        "event":           evt,
        "tgtg_kr":         round(tgtg_kr),
        "tgtg_ok":         tgtg_kr < 800,
        "tgtg_advarsel":   tgtg_kr > 1200,
        "tgtg_korrektion": round(tgtg_korr, 2),
        "vaekst_pct":      round(vaekst * 100, 1),
        "total_faktor":    round(total_faktor, 3),
        "produkter":       produkter,
        "total_stk":       total_stk,
        "total_kr":        round(total_kr, 2),
    }
