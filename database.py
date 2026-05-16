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

            CREATE TABLE IF NOT EXISTS tgtg_poser (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id      TEXT    DEFAULT '',
                navn         TEXT    NOT NULL,
                kreditpris   REAL    NOT NULL DEFAULT 0,
                aktiv        INTEGER DEFAULT 1,
                UNIQUE(navn) ON CONFLICT REPLACE
            );

            CREATE TABLE IF NOT EXISTS tgtg_dagssalg (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                dato         TEXT    NOT NULL,
                item_id      TEXT    DEFAULT '',
                pose_navn    TEXT    NOT NULL DEFAULT '',
                antal        INTEGER DEFAULT 0,
                kreditering  REAL    DEFAULT 0,
                indlæst      TEXT    DEFAULT (datetime('now','localtime')),
                UNIQUE(dato, pose_navn) ON CONFLICT REPLACE
            );
            CREATE INDEX IF NOT EXISTS idx_tgtg_dato ON tgtg_dagssalg(dato);

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

            CREATE TABLE IF NOT EXISTS varestamdata (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sku          TEXT    DEFAULT '',
                varenavn     TEXT    NOT NULL,
                type         TEXT    NOT NULL DEFAULT '',
                pris_ex_moms REAL    DEFAULT 0,
                UNIQUE(varenavn) ON CONFLICT REPLACE
            );

            CREATE TABLE IF NOT EXISTS faste_omkostninger (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                aar      INTEGER NOT NULL,
                maaned   INTEGER NOT NULL,
                kategori TEXT    NOT NULL DEFAULT 'Faste omk.',
                beloeb   REAL    NOT NULL DEFAULT 0,
                UNIQUE(aar, maaned, kategori) ON CONFLICT REPLACE
            );

            DROP VIEW IF EXISTS v_transaktioner;
            CREATE VIEW v_transaktioner AS
            SELECT t.*,
                   t.omsætning / 1.25                      AS omsaetning_ex_moms,
                   CASE WHEN s.pris_ex_moms > 0
                        THEN t.antal * s.pris_ex_moms
                        ELSE t.kostpris END                AS vf_korrekt,
                   t.omsætning / 1.25
                       - CASE WHEN s.pris_ex_moms > 0
                              THEN t.antal * s.pris_ex_moms
                              ELSE t.kostpris END          AS db_korrekt
            FROM transaktioner t
            LEFT JOIN varestamdata s
                ON t.varenummer != '' AND t.varenummer = s.sku;
        """)
        # Migrationer til eksisterende tabeller
        for sql in [
            "ALTER TABLE transaktioner ADD COLUMN time_start INTEGER DEFAULT -1",
            "ALTER TABLE transaktioner ADD COLUMN bon_nr TEXT DEFAULT ''",
            "ALTER TABLE ugebestillinger ADD COLUMN sektion INTEGER DEFAULT 1",
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

def hent_kpi(aar: int = None) -> Dict:
    with _conn() as conn:
        aar_filter = "WHERE strftime('%Y', dato) = ?" if aar else ""
        aar_params = (str(aar),) if aar else ()
        seneste_dato = conn.execute(
            f"SELECT MAX(dato) FROM transaktioner {aar_filter}", aar_params
        ).fetchone()[0]

        if not seneste_dato:
            return {"dag": None, "uge": None, "snit_uge": None}

        dag = conn.execute("""
            SELECT COALESCE(SUM(omsætning),0)    AS omsaetning,
                   COALESCE(SUM(vf_korrekt),0)   AS vareforbrug,
                   CASE WHEN COUNT(CASE WHEN bon_nr != '' THEN 1 END) > 0
                        THEN COUNT(DISTINCT CASE WHEN bon_nr != '' THEN bon_nr END)
                        ELSE COUNT(*)
                   END                            AS transak,
                   COALESCE(SUM(db_korrekt),0)   AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                        ELSE 0 END                AS db_pct
            FROM v_transaktioner WHERE dato = ?
        """, (seneste_dato,)).fetchone()

        seneste_yw = conn.execute(
            "SELECT strftime('%Y-%W', ?)", (seneste_dato,)
        ).fetchone()[0]

        uge = conn.execute("""
            SELECT COALESCE(SUM(omsætning),0)   AS omsaetning,
                   COALESCE(SUM(vf_korrekt),0)  AS vareforbrug,
                   COALESCE(SUM(db_korrekt),0)  AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                        ELSE 0 END               AS db_pct,
                   COUNT(DISTINCT dato)          AS antal_dage
            FROM v_transaktioner
            WHERE strftime('%Y-%W', dato) = ?
        """, (seneste_yw,)).fetchone()

        snit_where = f"WHERE strftime('%Y', dato) = '{aar}'" if aar else ""
        snit_row = conn.execute(f"""
            SELECT AVG(uge_total) AS snit_uge FROM (
                SELECT SUM(omsætning) AS uge_total
                FROM transaktioner
                {snit_where}
                GROUP BY strftime('%Y-%W', dato)
                ORDER BY dato DESC LIMIT 12
            )
        """).fetchone()

        # Dagssnit: gennemsnit af dagstotaler over seneste 28 dage med data
        dag_snit_extra = f"AND strftime('%Y', dato) = '{aar}'" if aar else ""
        dag_snit_row = conn.execute(f"""
            SELECT AVG(dag_total) AS snit_dag FROM (
                SELECT SUM(omsætning) AS dag_total
                FROM transaktioner
                WHERE 1=1 {dag_snit_extra}
                GROUP BY dato
                ORDER BY dato DESC LIMIT 28
            )
        """).fetchone()

        # Forrige uge med data (til uge-over-uge DB-sammenligning)
        prev_extra = f"AND strftime('%Y', dato) = '{aar}'" if aar else ""
        prev_uge_row = conn.execute(f"""
            SELECT COALESCE(SUM(omsætning),0)  AS omsaetning,
                   COALESCE(SUM(db_korrekt),0) AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                        ELSE 0 END             AS db_pct
            FROM v_transaktioner
            WHERE strftime('%Y-%W', dato) = (
                SELECT DISTINCT strftime('%Y-%W', dato)
                FROM transaktioner
                WHERE strftime('%Y-%W', dato) < ? {prev_extra}
                ORDER BY dato DESC LIMIT 1
            )
        """, (seneste_yw,)).fetchone()

        # Samme dag forrige uge (seneste_dato - 7 dage)
        prev_dag_dato = conn.execute(
            "SELECT date(?, '-7 days')", (seneste_dato,)
        ).fetchone()[0]
        prev_dag_row = conn.execute("""
            SELECT COALESCE(SUM(omsætning),0)  AS omsaetning,
                   COALESCE(SUM(db_korrekt),0) AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                        ELSE 0 END             AS db_pct,
                   CASE WHEN COUNT(CASE WHEN bon_nr != '' THEN 1 END) > 0
                        THEN COUNT(DISTINCT CASE WHEN bon_nr != '' THEN bon_nr END)
                        ELSE COUNT(*)
                   END                         AS transak
            FROM v_transaktioner WHERE dato = ?
        """, (prev_dag_dato,)).fetchone()

        # Samme dag 2 uger siden (seneste_dato - 14 dage)
        prev_prev_dag_dato = conn.execute(
            "SELECT date(?, '-7 days')", (prev_dag_dato,)
        ).fetchone()[0]
        prev_prev_dag_row = conn.execute("""
            SELECT COALESCE(SUM(omsætning),0)  AS omsaetning,
                   COALESCE(SUM(db_korrekt),0) AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                        ELSE 0 END             AS db_pct
            FROM v_transaktioner WHERE dato = ?
        """, (prev_prev_dag_dato,)).fetchone()

        # MTD: fra 1. i indeværende måned til seneste dag
        mtd_start = seneste_dato[:8] + '01'  # YYYY-MM-01
        mtd_row = conn.execute("""
            SELECT COALESCE(SUM(omsætning),0)  AS omsaetning,
                   COALESCE(SUM(db_korrekt),0) AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                        ELSE 0 END             AS db_pct,
                   COUNT(DISTINCT dato)        AS antal_dage
            FROM v_transaktioner WHERE dato >= ? AND dato <= ?
        """, (mtd_start, seneste_dato)).fetchone()

        # Forrige måned – samme periode (1. til dato -1 måned)
        prev_mtd_start = conn.execute(
            "SELECT date(?, '-1 month')", (mtd_start,)
        ).fetchone()[0]
        prev_mtd_end = conn.execute(
            "SELECT date(?, '-1 month')", (seneste_dato,)
        ).fetchone()[0]
        prev_mtd_row = conn.execute("""
            SELECT COALESCE(SUM(omsætning),0)  AS omsaetning,
                   COALESCE(SUM(db_korrekt),0) AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                        ELSE 0 END             AS db_pct
            FROM v_transaktioner WHERE dato >= ? AND dato <= ?
        """, (prev_mtd_start, prev_mtd_end)).fetchone()

    return {
        "dag":           dict(dag)               if dag               else None,
        "uge":           dict(uge)               if uge               else None,
        "prev_uge":      dict(prev_uge_row)      if prev_uge_row      else None,
        "prev_dag":      dict(prev_dag_row)      if prev_dag_row      else None,
        "prev_dag_dato": prev_dag_dato,
        "prev_prev_dag": dict(prev_prev_dag_row) if prev_prev_dag_row else None,
        "mtd":           dict(mtd_row)           if mtd_row           else None,
        "prev_mtd":      dict(prev_mtd_row)      if prev_mtd_row      else None,
        "snit_uge":      snit_row["snit_uge"]    if snit_row          else None,
        "snit_dag":      dag_snit_row["snit_dag"] if dag_snit_row     else None,
    }


def hent_dag_produkter(aar: int = None) -> Dict:
    """Produkter solgt seneste dag, sorteret efter omsætning."""
    with _conn() as conn:
        aar_filter = "WHERE strftime('%Y', dato) = ?" if aar else ""
        aar_params = (str(aar),) if aar else ()
        seneste_dato = conn.execute(
            f"SELECT MAX(dato) FROM transaktioner {aar_filter}", aar_params
        ).fetchone()[0]
        if not seneste_dato:
            return {"dato": None, "produkter": []}
        rows = conn.execute("""
            SELECT varenavn,
                   MAX(kategori)               AS kategori,
                   ROUND(SUM(antal), 0)        AS antal,
                   ROUND(SUM(omsætning), 0)    AS omsaetning,
                   ROUND(SUM(vf_korrekt), 0)   AS vareforbrug,
                   ROUND(SUM(db_korrekt), 0)   AS db_kr,
                   ROUND(CASE WHEN SUM(omsætning)>0 THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100 ELSE 0 END, 1) AS db_pct
            FROM v_transaktioner
            WHERE dato = ?
            GROUP BY varenavn
            ORDER BY omsaetning DESC
        """, (seneste_dato,)).fetchall()
    return {"dato": seneste_dato, "produkter": [dict(r) for r in rows]}


def hent_dage(n: int = 14, aar: int = None) -> List[Dict]:
    with _conn() as conn:
        where = "WHERE strftime('%Y', dato) = ?" if aar else ""
        params = (str(aar), n) if aar else (n,)
        rows = conn.execute(f"""
            SELECT dato, SUM(omsætning) AS omsaetning
            FROM transaktioner
            {where}
            GROUP BY dato
            ORDER BY dato DESC
            LIMIT ?
        """, params).fetchall()
    return [dict(r) for r in reversed(rows)]


def _mp_uge_netto(aar: int, maaned: int) -> float:
    """Pro-ratet MobilePay netto (ex. moms) for én uge i given måned."""
    from calendar import monthrange
    with _conn() as conn:
        row = conn.execute(
            "SELECT omsaetning FROM mobilepay WHERE aar=? AND maaned=?",
            (aar, maaned)
        ).fetchone()
    if not row:
        return 0.0
    days = monthrange(aar, maaned)[1]
    return round((row["omsaetning"] / 1.25) / days * 7, 2)


def _mp_map_alle() -> Dict:
    """Returnerer {(aar, maaned): omsaetning_inkl_moms}."""
    with _conn() as conn:
        rows = conn.execute("SELECT aar, maaned, omsaetning FROM mobilepay").fetchall()
    return {(r["aar"], r["maaned"]): r["omsaetning"] for r in rows}


def hent_uger(aar: int = None) -> List[Dict]:
    from datetime import date as _date
    from calendar import monthrange
    with _conn() as conn:
        where = "WHERE strftime('%Y', dato) = ?" if aar else ""
        params = (str(aar),) if aar else ()
        rows = conn.execute(f"""
            SELECT
                strftime('%Y', dato)  AS aar,
                CAST(strftime('%W', dato) AS INTEGER) AS uge,
                MIN(dato)                             AS min_dato,
                ROUND(SUM(omsætning), 2)              AS omsaetning,
                ROUND(SUM(vf_korrekt), 2)             AS vareforbrug,
                ROUND(SUM(db_korrekt), 2)             AS db_kr,
                ROUND(CASE WHEN SUM(omsætning)>0
                     THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                     ELSE 0 END, 1)                   AS db_pct,
                COUNT(DISTINCT dato)                  AS antal_dage
            FROM v_transaktioner
            {where}
            GROUP BY strftime('%Y-%W', dato)
            ORDER BY dato ASC
        """, params).fetchall()

    mp = _mp_map_alle()
    resultat = []
    for r in rows:
        d = _date.fromisoformat(r["min_dato"])
        days = monthrange(d.year, d.month)[1]
        mp_inkl = mp.get((d.year, d.month), 0.0)
        mp_netto = round((mp_inkl / 1.25) / days * 7, 0) if mp_inkl else 0.0
        row = dict(r)
        row["mp_netto"] = mp_netto
        resultat.append(row)
    return resultat


def hent_timer_idag(aar: int = None) -> List[Dict]:
    with _conn() as conn:
        aar_filter = "WHERE strftime('%Y', dato) = ?" if aar else ""
        aar_params = (str(aar),) if aar else ()
        seneste_dato = conn.execute(
            f"SELECT MAX(dato) FROM transaktioner {aar_filter}", aar_params
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


def hent_timer_forrige_uge(aar: int = None) -> List[Dict]:
    """Timeomsætning for samme ugedag 7 dage før seneste dato."""
    from datetime import date as _date, timedelta as _td
    with _conn() as conn:
        aar_filter = "WHERE strftime('%Y', dato) = ?" if aar else ""
        aar_params = (str(aar),) if aar else ()
        seneste_dato = conn.execute(
            f"SELECT MAX(dato) FROM transaktioner {aar_filter}", aar_params
        ).fetchone()[0]
        if not seneste_dato:
            return []
        prev_dato = (_date.fromisoformat(seneste_dato) - _td(days=7)).isoformat()
        rows = conn.execute("""
            SELECT time_start, ROUND(SUM(omsætning), 2) AS omsaetning
            FROM transaktioner
            WHERE dato = ? AND time_start >= 0
            GROUP BY time_start
            ORDER BY time_start
        """, (prev_dato,)).fetchall()
    return [dict(r) for r in rows]


def hent_timer_snit(aar: int = None) -> List[Dict]:
    with _conn() as conn:
        extra = "AND strftime('%Y', dato) = ?" if aar else ""
        params = (str(aar),) if aar else ()
        rows = conn.execute(f"""
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
                WHERE time_start >= 0 {extra}
                GROUP BY time_start, dato
            )
            GROUP BY time_start, ugedag
            ORDER BY time_start, ugedag
        """, params).fetchall()
    return [dict(r) for r in rows]


def hent_kategorier(aar: int = None) -> List[Dict]:
    with _conn() as conn:
        extra = "AND strftime('%Y', dato) = ?" if aar else ""
        params = (str(aar),) if aar else ()
        rows = conn.execute(f"""
            SELECT kategori, ROUND(SUM(omsætning), 2) AS omsaetning,
                   ROUND(SUM(db_korrekt), 2) AS db_kr,
                   ROUND(SUM(db_korrekt)*1.25/NULLIF(SUM(omsætning),0)*100, 1) AS db_pct
            FROM v_transaktioner
            WHERE kategori != '' {extra}
            GROUP BY kategori
            ORDER BY omsaetning DESC
        """, params).fetchall()
    return [dict(r) for r in rows]


def hent_kategorier_uge(aar: int = None) -> List[Dict]:
    """DB per kategori for indeværende uge (seneste dato)."""
    with _conn() as conn:
        aar_filter = "WHERE strftime('%Y', dato) = ?" if aar else ""
        aar_params = (str(aar),) if aar else ()
        seneste_dato = conn.execute(
            f"SELECT MAX(dato) FROM transaktioner {aar_filter}", aar_params
        ).fetchone()[0]
        if not seneste_dato:
            return []
        yw = conn.execute(
            "SELECT strftime('%Y-%W', ?)", (seneste_dato,)
        ).fetchone()[0]
        rows = conn.execute("""
            SELECT kategori,
                   ROUND(SUM(omsætning), 2)                                        AS omsaetning,
                   ROUND(SUM(db_korrekt), 2)                                       AS db_kr,
                   ROUND(SUM(db_korrekt)*1.25/NULLIF(SUM(omsætning),0)*100, 1)    AS db_pct
            FROM v_transaktioner
            WHERE kategori != '' AND strftime('%Y-%W', dato) = ?
            GROUP BY kategori
            ORDER BY db_kr DESC
        """, (yw,)).fetchall()
    return [dict(r) for r in rows]


def hent_dage_detaljer(n: int = 8, aar: int = None) -> List[Dict]:
    from datetime import datetime
    DAG_NAVNE    = ['Mandag','Tirsdag','Onsdag','Torsdag','Fredag','Lørdag','Søndag']
    MAANED_NAVNE = {1:'januar',2:'februar',3:'marts',4:'april',5:'maj',6:'juni',
                    7:'juli',8:'august',9:'september',10:'oktober',11:'november',12:'december'}

    with _conn() as conn:
        where = "WHERE strftime('%Y', dato) = ?" if aar else ""
        params = (str(aar), n) if aar else (n,)
        dage = conn.execute(f"""
            SELECT dato,
                   ROUND(SUM(omsætning), 2) AS omsaetning,
                   COUNT(*)                  AS linjer
            FROM transaktioner
            {where}
            GROUP BY dato
            ORDER BY dato DESC
            LIMIT ?
        """, params).fetchall()

        if not dage:
            return []

        dato_list    = [r['dato'] for r in dage]
        placeholders = ','.join('?' * len(dato_list))

        produkter = conn.execute(f"""
            SELECT dato, varenavn,
                   MAX(kategori)            AS kategori,
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
            'varenavn':   p['varenavn'],
            'kategori':   p['kategori'] or '',
            'antal':      int(p['antal']),
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
    from datetime import datetime, date as _date
    if aar is None:
        aar = datetime.now().year
    with _conn() as conn:
        rows = conn.execute("""
            SELECT
                CAST(strftime('%m', dato) AS INTEGER) AS maaned,
                COUNT(DISTINCT dato)                   AS faktiske_dage,
                ROUND(SUM(omsætning), 2)               AS omsaetning,
                ROUND(SUM(kostpris),  2)               AS kostpris,
                ROUND(SUM(avance)-SUM(omsætning)*0.2, 2) AS avance,
                ROUND((SUM(avance)-SUM(omsætning)*0.2)*1.25/NULLIF(SUM(omsætning),0)*100, 1) AS gpm
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
                   ROUND((SUM(avance)-SUM(omsætning)*0.2)*1.25/NULLIF(SUM(omsætning),0)*100, 1) AS gpm
            FROM transaktioner WHERE strftime('%Y-%m', dato) = ?
        """, (f"{aar-1}-12",)).fetchone()

        seneste = conn.execute("SELECT MAX(dato) FROM transaktioner").fetchone()[0]
        base_row = None
        if seneste:
            base_row = conn.execute("""
                SELECT
                    ROUND(SUM(omsætning)/NULLIF(COUNT(DISTINCT dato),0), 2) AS kr_pr_dag,
                    ROUND((SUM(avance)-SUM(omsætning)*0.2)*1.25/NULLIF(SUM(omsætning),0)*100, 1)      AS gpm
                FROM transaktioner
                WHERE dato >= date(?, '-28 days')
            """, (seneste,)).fetchone()

        # Faktisk vareforbrug per måned: bagerfakturaer fordeles proportionalt
        # efter faktiske daglige indkøb (dag_antal × pris_ex_moms fra bestillinger).
        # Fallback til 1/7 uniform hvis ingen bestillingsdata findes for ugen.
        from datetime import timedelta as _td
        bager_rows = conn.execute(
            "SELECT uge, aar, faktura FROM bager_regnskab WHERE aar=? OR aar=?",
            (aar, aar - 1)
        ).fetchall()

        # Daglig indkøbsværdi per uge fra ugebestillinger
        bestil_rows = conn.execute("""
            SELECT uge, aar,
                   SUM(man * pris_ex_moms) AS man,
                   SUM(tir * pris_ex_moms) AS tir,
                   SUM(ons * pris_ex_moms) AS ons,
                   SUM(tor * pris_ex_moms) AS tor,
                   SUM(fre * pris_ex_moms) AS fre,
                   SUM(loe * pris_ex_moms) AS loe,
                   SUM(son * pris_ex_moms) AS son
            FROM ugebestillinger
            GROUP BY uge, aar
        """).fetchall()
        _DAG_NAVNE = ["man", "tir", "ons", "tor", "fre", "loe", "son"]
        bestil_map = {(int(r["aar"]), int(r["uge"])): r for r in bestil_rows}

        faktura_maaned: Dict = {}
        for br in bager_rows:
            try:
                fakt = br["faktura"] or 0
                if fakt == 0:
                    continue
                key = (int(br["aar"]), int(br["uge"]))
                mon = _date.fromisocalendar(int(br["aar"]), int(br["uge"]), 1)
                bestil = bestil_map.get(key)
                dag_vaerdier = [float(bestil[d] or 0) for d in _DAG_NAVNE] if bestil else []
                total_bestil = sum(dag_vaerdier) if dag_vaerdier else 0.0
                for i, dag_navn in enumerate(_DAG_NAVNE):
                    dag = mon + _td(days=i)
                    if dag.year != aar:
                        continue
                    vaegt = (dag_vaerdier[i] / total_bestil) if total_bestil > 0 else (1.0 / 7.0)
                    faktura_maaned[dag.month] = round(
                        faktura_maaned.get(dag.month, 0.0) + fakt * vaegt, 2
                    )
            except Exception:
                pass

        # MobilePay netto per måned (÷1.25)
        mp_rows = conn.execute(
            "SELECT maaned, omsaetning FROM mobilepay WHERE aar=?", (aar,)
        ).fetchall()
        mp_netto_maaned: Dict = {r["maaned"]: round(r["omsaetning"] / 1.25, 0) for r in mp_rows}

        # Kostpris for IKKE-bagværk per måned (Shopbox er korrekt for disse)
        ikke_bager_rows = conn.execute("""
            SELECT CAST(strftime('%m', dato) AS INTEGER) AS maaned,
                   ROUND(SUM(kostpris), 2) AS vf
            FROM transaktioner
            WHERE strftime('%Y', dato) = ?
              AND CAST(CAST(varenummer AS REAL) AS INTEGER) NOT IN (
                  SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                  FROM ugebestillinger WHERE varenummer != '' AND varenummer != '0'
              )
            GROUP BY maaned
        """, (str(aar),)).fetchall()
        vf_ikke_bager: Dict = {r["maaned"]: r["vf"] for r in ikke_bager_rows}

    return {
        "aar":               aar,
        "maaneder":          [dict(r) for r in rows],
        "prev_dec":          dict(prev_dec) if prev_dec and prev_dec["omsaetning"] else None,
        "base_kr_pr_dag":    base_row["kr_pr_dag"] if base_row else None,
        "base_gpm":          base_row["gpm"]       if base_row else None,
        "faktura_maaned":    faktura_maaned,
        "mp_netto_maaned":   mp_netto_maaned,
        "vf_ikke_bager":     vf_ikke_bager,
        "faste_omk":         hent_faste_omk(aar),
        "faste_omk_sum":     faste_omk_maaned_sum(aar),
    }


def hent_trend_analyse(periode_dage: int = 21, aar: int = None) -> Dict:
    """Sammenlign seneste periode mod forrige periode (dagsnormaliseret)."""
    from datetime import datetime, timedelta
    with _conn() as conn:
        aar_filter = "WHERE strftime('%Y', dato) = ?" if aar else ""
        aar_params = (str(aar),) if aar else ()
        seneste_dato = conn.execute(
            f"SELECT MAX(dato) FROM transaktioner {aar_filter}", aar_params
        ).fetchone()[0]
        tidligste_dato = conn.execute(
            f"SELECT MIN(dato) FROM transaktioner {aar_filter}", aar_params
        ).fetchone()[0]
        if not seneste_dato:
            return {}

        slut  = datetime.strptime(seneste_dato, '%Y-%m-%d')
        midt  = slut  - timedelta(days=periode_dage)
        start = midt  - timedelta(days=periode_dage)
        midt_str  = midt.strftime('%Y-%m-%d')
        start_str = start.strftime('%Y-%m-%d')

        aar_extra = f"AND strftime('%Y', dato) = '{aar}'" if aar else ""
        rows = conn.execute(f"""
            SELECT
                varenavn, kategori,
                ROUND(SUM(CASE WHEN dato > ? THEN antal      ELSE 0 END), 1) AS ny_antal,
                ROUND(SUM(CASE WHEN dato > ? THEN omsætning  ELSE 0 END), 2) AS ny_omsat,
                ROUND(SUM(CASE WHEN dato > ? AND dato <= ? THEN antal      ELSE 0 END), 1) AS gl_antal,
                ROUND(SUM(CASE WHEN dato > ? AND dato <= ? THEN omsætning  ELSE 0 END), 2) AS gl_omsat,
                ROUND((SUM(avance)-SUM(omsætning)*0.2)*1.25/NULLIF(SUM(omsætning),0)*100, 1) AS db_pct
            FROM transaktioner
            WHERE dato > ? AND varenavn != '' {aar_extra}
            GROUP BY varenavn
            HAVING ny_omsat > 0 OR gl_omsat > 0
        """, (midt_str, midt_str,
              start_str, midt_str,
              start_str, midt_str,
              start_str)).fetchall()

        ny_dage = conn.execute(
            f"SELECT COUNT(DISTINCT dato) FROM transaktioner WHERE dato > ? {aar_extra}", (midt_str,)
        ).fetchone()[0] or 1
        gl_dage = conn.execute(
            f"SELECT COUNT(DISTINCT dato) FROM transaktioner WHERE dato > ? AND dato <= ? {aar_extra}",
            (start_str, midt_str)
        ).fetchone()[0] or 1

        ny_total = conn.execute(
            f"SELECT COALESCE(SUM(omsætning),0) FROM transaktioner WHERE dato > ? {aar_extra}", (midt_str,)
        ).fetchone()[0]
        gl_total = conn.execute(
            f"SELECT COALESCE(SUM(omsætning),0) FROM transaktioner WHERE dato > ? AND dato <= ? {aar_extra}",
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


def hent_kaffe_analyse(aar: int = None) -> Dict:
    with _conn() as conn:
        aar_extra = f"AND strftime('%Y', dato) = '{aar}'" if aar else ""
        kpi = conn.execute(f"""
            SELECT
                ROUND(SUM(antal), 0)                                      AS total_antal,
                ROUND(SUM(omsætning), 2)                                  AS total_omsaetning,
                ROUND(SUM(avance)-SUM(omsætning)*0.2, 2)                  AS total_avance,
                ROUND((SUM(avance)-SUM(omsætning)*0.2)*1.25/NULLIF(SUM(omsætning),0)*100, 1)       AS db_pct,
                ROUND(SUM(omsætning)/NULLIF(SUM(antal),0), 2)            AS gns_pris
            FROM transaktioner
            WHERE {_KAFFE_WHERE} {aar_extra}
        """).fetchone()

        total_omsat = conn.execute(
            f"SELECT COALESCE(SUM(omsætning),0) FROM transaktioner WHERE 1=1 {aar_extra}"
        ).fetchone()[0]

        produkter = conn.execute(f"""
            SELECT varenavn,
                   ROUND(SUM(antal), 0)                                   AS antal,
                   ROUND(SUM(omsætning), 2)                               AS omsaetning,
                   ROUND((SUM(avance)-SUM(omsætning)*0.2)*1.25/NULLIF(SUM(omsætning),0)*100, 1)    AS db_pct
            FROM transaktioner
            WHERE {_KAFFE_WHERE} {aar_extra}
            GROUP BY varenavn
            ORDER BY omsaetning DESC
        """).fetchall()

        dage_rows = conn.execute(f"""
            SELECT dato,
                   ROUND(SUM(antal), 0)    AS antal,
                   ROUND(SUM(omsætning), 2) AS omsaetning
            FROM transaktioner
            WHERE {_KAFFE_WHERE} {aar_extra}
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
            WHERE {_KAFFE_WHERE} AND time_start >= 0 {aar_extra}
            GROUP BY time_start
            ORDER BY time_start
        """).fetchall()

        timer_produkter = conn.execute(f"""
            SELECT time_start, varenavn,
                   ROUND(SUM(antal), 0) AS total_antal
            FROM transaktioner
            WHERE {_KAFFE_WHERE} AND time_start >= 0 {aar_extra}
            GROUP BY time_start, varenavn
            ORDER BY time_start, total_antal DESC
        """).fetchall()

        dage_produkter = conn.execute(f"""
            SELECT dato, varenavn,
                   ROUND(SUM(antal), 0) AS total_antal
            FROM transaktioner
            WHERE {_KAFFE_WHERE} {aar_extra}
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


def hent_top_produkter(n: int = 20, aar: int = None) -> List[Dict]:
    with _conn() as conn:
        extra = "AND strftime('%Y', dato) = ?" if aar else ""
        params = (str(aar), n) if aar else (n,)
        rows = conn.execute(f"""
            SELECT varenavn,
                   MAX(kategori)                                              AS kategori,
                   ROUND(SUM(omsætning), 2)                                  AS omsaetning,
                   ROUND(SUM(vf_korrekt), 2)                                 AS vareforbrug,
                   ROUND(SUM(antal), 0)                                      AS antal,
                   ROUND(SUM(db_korrekt), 2)                                 AS db_kr,
                   ROUND(SUM(db_korrekt)*1.25/NULLIF(SUM(omsætning),0)*100, 1) AS db_pct
            FROM v_transaktioner
            WHERE varenavn != '' {extra}
            GROUP BY varenavn
            ORDER BY omsaetning DESC
            LIMIT ?
        """, params).fetchall()
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
            # avance fra Shopbox = omsætning_inkl - kostpris_ex (blander moms)
            # Korrekt ex-moms GPM: (avance - omsætning*0.2) / (omsætning/1.25) * 100
            avance_ex = totaler["avance"] - totaler["omsætning"] * 0.2
            avance_pct = avance_ex / (totaler["omsætning"] / 1.25) * 100

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
        # Ryd eksisterende rækker først — forhindrer dubletter ved force-sync
        conn.execute("DELETE FROM ugebestillinger WHERE uge=? AND aar=?", (uge, aar))
        for linje in linjer:
            conn.execute("""
                INSERT INTO ugebestillinger
                    (uge, aar, varenummer, varenavn, pris_ex_moms,
                     man, tir, ons, tor, fre, loe, son, total_antal, total_pris, sektion)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                linje.get("sektion", 1),
            ))
    return len(linjer)


def hent_bestilling_uger(aar: int = None) -> List[Dict]:
    with _conn() as conn:
        extra = "WHERE aar = ?" if aar else ""
        params = (aar,) if aar else ()
        rows = conn.execute(f"""
            SELECT uge, aar,
                   COUNT(*)                    AS antal_varer,
                   ROUND(SUM(total_antal), 0)  AS total_antal,
                   ROUND(SUM(total_pris), 2)   AS total_pris,
                   MAX(indlæst)                AS indlæst
            FROM ugebestillinger
            {extra}
            GROUP BY uge, aar
            ORDER BY aar DESC, uge DESC
        """, params).fetchall()
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


def hent_bagvaerk_dag_sammenligning(uge: int, aar: int) -> Dict:
    """Bestilt vs. solgt per produkt per dag for en given uge."""
    from datetime import date as _date, timedelta as _td

    DAGE = ['man', 'tir', 'ons', 'tor', 'fre', 'loe', 'son']
    DAGE_NAVNE = ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn']
    try:
        mandag = _date.fromisocalendar(int(aar), int(uge), 1)
    except Exception:
        return {"uge": uge, "aar": aar, "dage": [], "produkter": []}
    dage_datoer = [(mandag + _td(days=i)).isoformat() for i in range(7)]

    with _conn() as conn:
        bestil = conn.execute("""
            SELECT varenummer, varenavn, COALESCE(sektion,1) AS sektion,
                   COALESCE(man,0) AS man, COALESCE(tir,0) AS tir,
                   COALESCE(ons,0) AS ons, COALESCE(tor,0) AS tor,
                   COALESCE(fre,0) AS fre, COALESCE(loe,0) AS loe,
                   COALESCE(son,0) AS son
            FROM ugebestillinger
            WHERE uge = ? AND aar = ?
            ORDER BY COALESCE(sektion,1) ASC, rowid ASC
        """, (uge, aar)).fetchall()

        if not bestil:
            return {"uge": uge, "aar": aar, "dage": DAGE_NAVNE, "dage_datoer": dage_datoer, "produkter": []}

        skus = [str(b["varenummer"]) for b in bestil if b["varenummer"]]

        # Salg per varenummer per dato for ugen
        if skus:
            placeholders_dato = ','.join('?' * len(dage_datoer))
            placeholders_sku  = ','.join('?' * len(skus))
            salg_rows = conn.execute(f"""
                SELECT varenummer, dato, ROUND(SUM(antal), 0) AS antal
                FROM transaktioner
                WHERE dato IN ({placeholders_dato})
                  AND varenummer IN ({placeholders_sku})
                GROUP BY varenummer, dato
            """, dage_datoer + skus).fetchall()
        else:
            salg_rows = []

    salg_map: Dict = {}
    for s in salg_rows:
        vnr = str(s["varenummer"])
        salg_map.setdefault(vnr, {})[s["dato"]] = int(s["antal"] or 0)

    produkter = []
    for b in bestil:
        vnr = str(b["varenummer"]) if b["varenummer"] else ""
        dage_data = []
        tot_bestilt = tot_solgt = 0
        for i, dag in enumerate(DAGE):
            bestilt = int(b[dag] or 0)
            solgt   = salg_map.get(vnr, {}).get(dage_datoer[i], 0)
            diff    = solgt - bestilt   # negativt = solgte mindre end bestilt = rødt
            tot_bestilt += bestilt
            tot_solgt   += solgt
            dage_data.append({"bestilt": bestilt, "solgt": solgt, "diff": diff})
        produkter.append({
            "varenummer":  vnr,
            "varenavn":    b["varenavn"],
            "sektion":     int(b["sektion"] or 1),
            "dage":        dage_data,
            "tot_bestilt": tot_bestilt,
            "tot_solgt":   tot_solgt,
            "tot_diff":    tot_solgt - tot_bestilt,   # negativt = under-solgt
        })

    return {
        "uge":        uge,
        "aar":        aar,
        "dage_navne": DAGE_NAVNE,
        "dage_datoer": dage_datoer,
        "produkter":  produkter,
    }


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


def gem_tgtg_poser(poser: List[Dict]) -> int:
    """Gem/opdater pose-definitioner (navn, kreditpris, item_id)."""
    with _conn() as conn:
        for p in poser:
            conn.execute("""
                INSERT INTO tgtg_poser (item_id, navn, kreditpris, aktiv)
                VALUES (?,?,?,1)
                ON CONFLICT(navn) DO UPDATE SET
                    item_id=excluded.item_id,
                    kreditpris=excluded.kreditpris,
                    aktiv=1
            """, (p.get("item_id",""), p["navn"], p["kreditpris"]))
    return len(poser)


def gem_tgtg_dagssalg(linjer: List[Dict]) -> int:
    """Gem dagligt TGTG-salg. linjer: [{dato, item_id, pose_navn, antal}]"""
    with _conn() as conn:
        # Hent kreditpriser
        priser = {r["navn"]: r["kreditpris"] for r in
                  conn.execute("SELECT navn, kreditpris FROM tgtg_poser").fetchall()}
        for r in linjer:
            kreditpris   = priser.get(r["pose_navn"], 0)
            kreditering  = round(r["antal"] * kreditpris, 2)
            conn.execute("""
                INSERT INTO tgtg_dagssalg (dato, item_id, pose_navn, antal, kreditering)
                VALUES (?,?,?,?,?)
                ON CONFLICT(dato, pose_navn) DO UPDATE SET
                    antal=excluded.antal,
                    kreditering=excluded.kreditering
            """, (r["dato"], r.get("item_id",""), r["pose_navn"], r["antal"], kreditering))

        # bager_regnskab.tgtg opdateres IKKE herfra — den manuelle bager-faktura
        # (fra bager_retur_sync.py) er kilden til bager_kr i sammenligningen
    return len(linjer)


def hent_tgtg_overblik(aar: int = None) -> Dict:
    """Returner dagssalg + ugessummer matchet mod bager-faktura + pose-typer."""
    from datetime import date as _date

    with _conn() as conn:
        aar_filter = "AND strftime('%Y',dato)=?" if aar else ""
        aar_params = (str(aar),) if aar else ()

        # Dagssalg (seneste 60 dage)
        dage_rows = conn.execute(f"""
            SELECT dato, SUM(antal) AS total_antal, SUM(kreditering) AS total_kr
            FROM tgtg_dagssalg
            WHERE 1=1 {aar_filter}
            GROUP BY dato ORDER BY dato DESC LIMIT 60
        """, aar_params).fetchall()

        # Alle dagssalg til ISO-uge aggregering
        alle_dage = conn.execute(f"""
            SELECT dato, SUM(antal) AS antal, SUM(kreditering) AS kreditering
            FROM tgtg_dagssalg
            WHERE 1=1 {aar_filter}
            GROUP BY dato
        """, aar_params).fetchall()

        # Bager-faktura TGTG per uge
        bager_rows = conn.execute(f"""
            SELECT uge, aar, tgtg AS bager_kr
            FROM bager_regnskab
            WHERE tgtg > 0 {"AND aar=?" if aar else ""}
        """, (aar,) if aar else ()).fetchall()
        bager_map = {(r["uge"], r["aar"]): r["bager_kr"] for r in bager_rows}

        per_pose = conn.execute(f"""
            SELECT pose_navn, SUM(antal) AS total_antal, SUM(kreditering) AS total_kr
            FROM tgtg_dagssalg
            WHERE 1=1 {aar_filter}
            GROUP BY pose_navn ORDER BY total_kr DESC
        """, aar_params).fetchall()

        poser = conn.execute(
            "SELECT item_id, navn, kreditpris FROM tgtg_poser WHERE aktiv=1 ORDER BY navn"
        ).fetchall()

    # Aggreger til ISO-uger i Python
    uge_map: Dict = {}
    for r in alle_dage:
        iso = _date.fromisoformat(r["dato"]).isocalendar()
        key = (iso[1], iso[0])  # (uge, aar)
        if key not in uge_map:
            uge_map[key] = {"uge": key[0], "aar": key[1], "total_antal": 0, "beregnet_kr": 0.0}
        uge_map[key]["total_antal"] += int(r["antal"] or 0)
        uge_map[key]["beregnet_kr"] += float(r["kreditering"] or 0)

    # Match mod bager-faktura og beregn difference
    uger = []
    for key in sorted(uge_map.keys(), reverse=True)[:20]:
        u = uge_map[key].copy()
        u["beregnet_kr"] = round(u["beregnet_kr"], 2)
        bager_kr         = bager_map.get(key)
        u["bager_kr"]    = round(bager_kr, 2) if bager_kr else None
        u["diff_kr"]     = round(bager_kr - u["beregnet_kr"], 2) if bager_kr else None
        uger.append(u)

    return {
        "dage":     [dict(r) for r in dage_rows],
        "uger":     uger,
        "per_pose": [dict(r) for r in per_pose],
        "poser":    [dict(r) for r in poser],
    }


def hent_svind_data(aar: int = None) -> List[Dict]:
    """Kombinerer bestilling, bager_regnskab og kassesalg per uge.
    Effektivt solgt = kassesalg_stk + KW-kombostk + TGTG_stk.
    TGTG stk: faktiske enheder fra tgtg_dagssalg (dato = produktionsdato = salgsdag-1).
    Fallback: tgtg_kr ÷ 38 kr/pose hvis ingen tgtg_dagssalg data.
    """
    TGTG_KR_PR_POSE = 38.0

    from datetime import date as _date

    with _conn() as conn:
        # Kassesalg bagværk per dag — matcher varenummer fra bestillinger
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

        # TGTG faktisk stk fra tgtg_dagssalg (dato er allerede produktionsdato = salgsdag-1)
        tgtg_dage = conn.execute("""
            SELECT dato, SUM(antal) AS antal
            FROM tgtg_dagssalg
            GROUP BY dato
        """).fetchall()
        tgtg_stk_map: Dict = {}
        for r in tgtg_dage:
            iso = _date.fromisoformat(r["dato"]).isocalendar()
            key = (iso[1], iso[0])
            tgtg_stk_map[key] = tgtg_stk_map.get(key, 0) + int(r["antal"] or 0)

        aar_filter1 = "AND b.aar = ?" if aar else ""
        aar_filter2 = "AND u.aar = ?" if aar else ""
        aar_params  = (aar,) if aar else ()
        rows = conn.execute(f"""
            SELECT
                b.uge, b.aar,
                ROUND(SUM(u.total_antal), 0)                   AS bestilt_stk,
                ROUND(SUM(u.total_pris),  2)                   AS bestilt_kr,
                b.retur_wiener, b.retur_boller, b.tgtg, b.b_kvali, b.retur_ialt,
                ROUND(SUM(u.total_pris) - b.retur_ialt, 2)    AS netto_kr
            FROM bager_regnskab b
            LEFT JOIN ugebestillinger u ON u.uge = b.uge AND u.aar = b.aar
            WHERE 1=1 {aar_filter1}
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
            ) {aar_filter2}
            GROUP BY u.uge, u.aar
            ORDER BY aar DESC, uge DESC
        """, aar_params + aar_params).fetchall()

    from calendar import monthrange as _monthrange
    from datetime import date as _today_date
    mp = _mp_map_alle()

    # Indeværende ISO-uge og år — uger der ikke er startet endnu filtreres fra
    _today      = _today_date.today()
    _iso_today  = _today.isocalendar()
    _cur_uge    = _iso_today[1]
    _cur_aar    = _iso_today[0]

    result = []
    for r in rows:
        d = dict(r)
        # Spring uger over der endnu ikke er begyndt
        try:
            uge_mandag = _today_date.fromisocalendar(int(d["aar"]), int(d["uge"]), 1)
            if uge_mandag > _today:
                continue
        except Exception:
            pass
        kassesalg = kasse_map.get((d["uge"], d["aar"]))
        kw_stk    = int(kw_map.get((d["uge"], d["aar"]), 0) or 0)
        # Foretruk faktiske TGTG stk — fallback til kr-estimat
        tgtg_stk_actual = tgtg_stk_map.get((d["uge"], d["aar"]))
        if tgtg_stk_actual is not None:
            tgtg_stk       = int(tgtg_stk_actual)
            tgtg_stk_kilde = "faktisk"
        else:
            tgtg_stk       = round(d["tgtg"] / TGTG_KR_PR_POSE) if d.get("tgtg") else 0
            tgtg_stk_kilde = "estimat"

        # MobilePay netto pro-ratet til ugen (mandag bestemmer måned)
        try:
            mon = _date.fromisocalendar(d["aar"], d["uge"], 1)
            days = _monthrange(mon.year, mon.month)[1]
            mp_inkl = mp.get((mon.year, mon.month), 0.0)
            mp_netto = round((mp_inkl / 1.25) / days * 7, 0) if mp_inkl else 0.0
        except Exception:
            mp_netto = 0.0

        d["kassesalg_stk"]  = kassesalg
        d["kw_stk"]         = kw_stk
        d["tgtg_stk"]       = tgtg_stk
        d["tgtg_stk_kilde"] = tgtg_stk_kilde
        d["mp_netto"]      = mp_netto
        # Netto justeret: hvad kostede brødet minus hvad vi fik ind (inkl. MobilePay)
        if d.get("netto_kr") is not None:
            d["netto_kr_adj"] = round(d["netto_kr"] - mp_netto, 0)
        else:
            d["netto_kr_adj"] = None

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

# Manuelle overrides — vinder over dynamiske begivenheder.
# Bruges til at finjustere faktorer for specifikke år/uger.
_EVENTS_OVERRIDE: Dict = {
    # Uge 20 2026: Kr. Himmelfart (tor) = helligdag + konfirmationsdag i Greve
    # Folk har FRI og fejrer — butikken er åben og travl tor morgen.
    # Brofridag fredag løfter yderligere.
    (20, 2026): {"factor": 1.25, "navn": "Kr. Himmelfart + konfirmation + brofridag",
                 "note": "Tor: helligdag+konfirmation — folk fri og handler. Fre: brofridag +45%",
                 "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.25,"fre":1.45,"loe":1.20,"son":1.0}},
    # Store Bededag 2026 (1. maj, fredag) — afskaffet helligdag, stadig folkelig fridag
    (18, 2026): {"factor": 1.10, "navn": "Store Bededag (1. maj)",
                 "note": "+10% — fridag i ugen",
                 "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.2,"loe":1.2,"son":1.0}},
}

# ─── Dynamiske, tilbagevendende begivenheder ────────────────────────────────

def _paaskedag(aar: int):
    """Beregn Påskedag (søndag) vha. Gaussisk algoritme."""
    from datetime import date as _date
    a = aar % 19
    b = aar // 100
    c = aar % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day   = ((h + l - 7 * m + 114) % 31) + 1
    return _date(aar, month, day)


def _anden_soendag_i_maaned(aar: int, maaned: int):
    """Returnerer datoen for den anden søndag i den givne måned."""
    from datetime import date as _date, timedelta
    d = _date(aar, maaned, 1)
    dage_til_son = (6 - d.weekday()) % 7   # dage til første søndag
    return d + timedelta(days=dage_til_son + 7)  # anden søndag


_events_cache: Dict = {}   # cache pr. år, nulstilles ikke (Railway genstarter ved deploy)


def _events_for_aar(aar: int) -> Dict:
    """Generer dynamiske tilbagevendende begivenheder for et givet år.

    Inkluderer: Fastelavn, Påske(uge), 2.påskedag, Kr.Himmelfart,
    Mors dag, Pinse, 2.Pinsedag, Grundlovsdag, Fars dag, Juleugen, Nytårsuge.
    """
    if aar in _events_cache:
        return _events_cache[aar]

    from datetime import date as _date, timedelta

    ev: Dict = {}
    paaske = _paaskedag(aar)

    def _yw(d):
        iso = d.isocalendar()
        return iso[1], iso[0]   # (uge, aar) — iso[0]=år kan afvige ved uge 52/1

    def _dname(d, fmt="%d. %b"):
        """Kort datostreng, fjerner evt. ledende nul."""
        s = d.strftime(fmt)
        return s.lstrip("0")

    # ── Fastelavn (søndag, 7 uger før påske) ────────────────────────────
    fastelavn = paaske - timedelta(weeks=7)
    uw, uy = _yw(fastelavn)
    ev[(uw, uy)] = {
        "factor": 1.18, "navn": "Fastelavn",
        "note": "Fastelavnsboller — ekstra lørdag og søndag",
        "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.0,"loe":1.35,"son":1.10},
    }

    # ── Påskeuge (Skærtorsdag til Påskelørdag) ──────────────────────────
    skaer = paaske - timedelta(days=3)          # Skærtorsdag
    langfre = paaske - timedelta(days=2)        # Langfredag
    uw, uy = _yw(skaer)
    ev[(uw, uy)] = {
        "factor": 1.20,
        "navn": f"Påskeuge ({_dname(skaer)}.–{_dname(langfre)}. {langfre.strftime('%b')})",
        "note": "Lang weekend — ekstra torsdag og fredag",
        "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.35,"fre":1.40,"loe":1.20,"son":1.0},
    }

    # ── Ugen efter påske (2. Påskedag — mandag lukket) ──────────────────
    p2man = paaske + timedelta(days=1)
    uw2, uy2 = _yw(p2man)
    if (uw2, uy2) != (uw, uy):              # kun hvis anden uge
        ev[(uw2, uy2)] = {
            "factor": 0.90, "navn": "2. Påskedag — mandag lukket",
            "note": "Reducer mandag-leverancen",
            "dag_fak": {"man":0.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.0,"loe":1.0,"son":1.0},
        }

    # ── Kristi Himmelfartsdag (påske + 39 dage, altid torsdag) ──────────
    himmelfart = paaske + timedelta(days=39)
    hw, hy = _yw(himmelfart)
    if (hw, hy) not in ev:
        ev[(hw, hy)] = {
            "factor": 1.18,
            "navn": f"Kristi Himmelfartsdag ({_dname(himmelfart)}. {himmelfart.strftime('%b')})",
            "note": "Tor: folk fri — travl formiddag. Fre er brofridag for mange +40%",
            "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.20,"fre":1.40,"loe":1.20,"son":1.0},
        }

    # ── Konfirmationssæson (uge 15–19 hvert år) ─────────────────────────
    # I Greve holdes konfirmation på TORSDAG.
    # Familier handler ONS (dagen før) + TOR morgen til festen.
    # FRE er rolig (festen var i går). LØR/SØN normal.
    # Ekstra: wienerbrød, hvide boller, flutes, formbrød til festbordet.
    for delta_w in range(5):   # uge 15, 16, 17, 18, 19
        kd = _date.fromisocalendar(aar, 15 + delta_w, 1)
        kw, ky = _yw(kd)
        if (kw, ky) not in ev:
            ev[(kw, ky)] = {
                "factor": 1.08,
                "navn":   "Konfirmationssæson",
                "note":   "Ons+Tor: ekstra wienerbrød, hvide boller, flutes til torsdagsfest · Fre rolig",
                "dag_fak": {"man":1.0,"tir":1.0,"ons":1.25,"tor":1.20,
                             "fre":0.90,"loe":1.05,"son":1.0},
            }
        else:
            ex = dict(ev[(kw, ky)])
            ex["dag_fak"] = dict(ex["dag_fak"])
            ex["dag_fak"]["ons"] = max(ex["dag_fak"].get("ons", 1.0), 1.20)
            ex["dag_fak"]["tor"] = max(ex["dag_fak"].get("tor", 1.0), 1.15)
            ex["factor"]  = max(ex["factor"], 1.08)
            ex["note"]    = ex["note"] + " · Ons+Tor: ekstra wiener+boller (konfirmation)"
            ev[(kw, ky)] = ex

    # ── Mors dag (anden søndag i maj) ───────────────────────────────────
    mors = _anden_soendag_i_maaned(aar, 5)
    mw, my = _yw(mors)
    if (mw, my) not in ev:
        ev[(mw, my)] = {
            "factor": 1.22,
            "navn": f"Mors dag ({_dname(mors)}. maj)",
            "note": "Høj søndag — kage og wienerbrød sælger stærkt",
            "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.10,"loe":1.25,"son":1.55},
        }
    else:
        # Mors dag falder i helligdagsuge — forstærk søndag og factor
        existing = dict(ev[(mw, my)])
        existing["dag_fak"] = dict(existing["dag_fak"])
        existing["dag_fak"]["son"] = max(existing["dag_fak"].get("son", 1.0), 1.45)
        existing["factor"] = max(existing["factor"], 1.22)
        existing["navn"] = existing["navn"] + " + Mors dag"
        existing["note"] = existing["note"] + " · Mors dag søndag"
        ev[(mw, my)] = existing

    # ── Pinse (påske + 49 dage, søndag) ─────────────────────────────────
    pinse = paaske + timedelta(days=49)
    pw, py = _yw(pinse)
    ev[(pw, py)] = {
        "factor": 1.15,
        "navn": f"Pinse ({_dname(pinse)}. {pinse.strftime('%b')})",
        "note": "Søndagsleverance dækker søndag + mandag",
        "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.10,"loe":1.20,"son":1.40},
    }

    # ── 2. Pinsedag (mandag efter pinse — lukket) ────────────────────────
    pinse_man = pinse + timedelta(days=1)
    pw2, py2 = _yw(pinse_man)
    if (pw2, py2) != (pw, py):
        ev[(pw2, py2)] = {
            "factor": 0.88, "navn": "2. Pinsedag — mandag lukket",
            "note": "Reducer første leverance mandag",
            "dag_fak": {"man":0.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.0,"loe":1.0,"son":1.0},
        }

    # ── Grundlovsdag (5. juni — oftest fredag) ───────────────────────────
    grundlov = _date(aar, 6, 5)
    gw, gy = _yw(grundlov)
    if (gw, gy) not in ev:
        ev[(gw, gy)] = {
            "factor": 1.25,
            "navn": f"Grundlovsdag ({_dname(grundlov)}. jun.)",
            "note": "Fredag fridag — årets bedste bagværksdag",
            "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.60,"loe":1.20,"son":1.0},
        }
    else:
        existing = dict(ev[(gw, gy)])
        existing["dag_fak"] = dict(existing["dag_fak"])
        existing["dag_fak"]["fre"] = max(existing["dag_fak"].get("fre", 1.0), 1.50)
        existing["factor"] = max(existing["factor"], 1.15)
        existing["navn"] = existing["navn"] + " + Grundlovsdag"
        ev[(gw, gy)] = existing

    # ── Fars dag (anden søndag i juni — dansk tradition) ─────────────────
    fars = _anden_soendag_i_maaned(aar, 6)
    fw, fy = _yw(fars)
    if (fw, fy) not in ev:
        ev[(fw, fy)] = {
            "factor": 1.10,
            "navn": f"Fars dag ({_dname(fars)}. jun.)",
            "note": "Ekstra søndag — kage og brød",
            "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.05,"loe":1.10,"son":1.30},
        }
    else:
        existing = dict(ev[(fw, fy)])
        existing["dag_fak"] = dict(existing["dag_fak"])
        existing["dag_fak"]["son"] = max(existing["dag_fak"].get("son", 1.0), 1.25)
        existing["factor"] = max(existing["factor"], 1.10)
        if "Fars dag" not in existing.get("navn", ""):
            existing["navn"] = existing["navn"] + " + Fars dag"
        ev[(fw, fy)] = existing

    # ── Juleugen (uge med juledag 25. dec.) ─────────────────────────────
    juledag = _date(aar, 12, 25)
    jw, jy = _yw(juledag)
    ev[(jw, jy)] = {
        "factor": 1.85, "navn": "Juleugen",
        "note": "Årets travleste uge — planlæg indkøb i oktober",
        "dag_fak": {"man":1.2,"tir":1.3,"ons":1.4,"tor":1.5,"fre":1.6,"loe":1.4,"son":1.0},
    }

    # ── Nytårsuge (uge 1 det efterfølgende år) ───────────────────────────
    nytaar = _date(aar + 1, 1, 4)           # 4. jan er altid i uge 1
    nyw, nyy = _yw(nytaar)
    ev[(nyw, nyy)] = {
        "factor": 0.45, "navn": "Nytårsuge",
        "note": "Halv bestilling — butik lukket/kort uge",
        "dag_fak": {"man":0.0,"tir":0.5,"ons":0.5,"tor":0.5,"fre":0.5,"loe":0.5,"son":0.0},
    }

    _events_cache[aar] = ev
    return ev


def _get_event(uge: int, aar: int) -> Optional[Dict]:
    """Hent begivenhed for (uge, aar) — override vinder over dynamisk."""
    if (uge, aar) in _EVENTS_OVERRIDE:
        return _EVENTS_OVERRIDE[(uge, aar)]
    return _events_for_aar(aar).get((uge, aar))

_RB = 0.10    # returrate boller (10% sendes retur)
_RW = 0.135   # returrate wienerbrød (13.5%)
_BUFFER = 1.05


def _kat(varenavn: str, stamdata_map: Dict = None) -> str:
    if stamdata_map:
        t = stamdata_map.get((varenavn or '').lower().strip())
        if t:
            # Normaliser stamdata-typer til de interne kategorinavne
            _norm = {"Wienerbrød": "Wiener", "Rugbrød": "Rugbrød",
                     "Brød": "Brød", "Boller": "Boller", "Flute": "Flute",
                     "Kage": "Kage", "Jul": "Kage"}
            return _norm.get(t, t)
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

        sd_map_anb = _stamdata_type_map()
        kat_sum = {"Boller": 0.0, "Wiener": 0.0, "Brød": 0.0,
                   "Kage": 0.0, "Rugbrød": 0.0, "Flute": 0.0}
        for r in kat_rows:
            kat_sum[_kat(r["varenavn"], sd_map_anb)] += (r["stk"] or 0)
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
        evt  = _get_event(u_uge, u_aar)
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


# ── VARESTAMDATA ──────────────────────────────────────────────────────────────

def hent_stamdata() -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT id, sku, varenavn, type, pris_ex_moms
            FROM varestamdata
            ORDER BY type, varenavn
        """).fetchall()
    return [dict(r) for r in rows]


def gem_stamdata_linje(sku: str, varenavn: str, type_: str, pris_ex_moms: float) -> int:
    with _conn() as conn:
        cur = conn.execute("""
            INSERT INTO varestamdata (sku, varenavn, type, pris_ex_moms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(varenavn) DO UPDATE SET
                sku          = excluded.sku,
                type         = excluded.type,
                pris_ex_moms = excluded.pris_ex_moms
        """, (sku or '', varenavn, type_, pris_ex_moms or 0))
        return cur.lastrowid


def slet_stamdata(id_: int):
    with _conn() as conn:
        conn.execute("DELETE FROM varestamdata WHERE id = ?", (id_,))


def gem_stamdata_bulk(linjer: List[Dict]) -> int:
    with _conn() as conn:
        conn.executemany("""
            INSERT INTO varestamdata (sku, varenavn, type, pris_ex_moms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(varenavn) DO UPDATE SET
                sku          = excluded.sku,
                type         = excluded.type,
                pris_ex_moms = excluded.pris_ex_moms
        """, [(r.get("sku", ""), r["varenavn"], r["type"], r.get("pris_ex_moms", 0))
              for r in linjer])
    return len(linjer)


def hent_varenummer_kontrol() -> Dict:
    """Kontrol: hvilke varenumre fra bestillinger matcher/mangler i transaktioner og vice versa."""
    with _conn() as conn:
        # Alle unikke varenumre fra bestillinger (seneste 12 måneder)
        bestil_rows = conn.execute("""
            SELECT DISTINCT varenummer, varenavn
            FROM ugebestillinger
            WHERE varenummer != '' AND varenummer IS NOT NULL
            ORDER BY varenummer
        """).fetchall()

        # Alle unikke varenumre fra transaktioner (seneste 90 dage)
        salg_rows = conn.execute("""
            SELECT DISTINCT varenummer, varenavn,
                   COUNT(*) as transaktioner,
                   MAX(dato) as seneste_dato
            FROM transaktioner
            WHERE varenummer != '' AND varenummer IS NOT NULL
              AND dato >= date('now', '-90 days')
            GROUP BY varenummer
            ORDER BY varenummer
        """).fetchall()

        # Alle varenumre nogensinde i transaktioner
        alle_salg_vnr = conn.execute("""
            SELECT DISTINCT varenummer FROM transaktioner
            WHERE varenummer != '' AND varenummer IS NOT NULL
        """).fetchall()

    bestil_map = {str(r["varenummer"]): r["varenavn"] for r in bestil_rows}
    salg_map   = {str(r["varenummer"]): dict(r) for r in salg_rows}
    alle_salg  = {str(r["varenummer"]) for r in alle_salg_vnr}

    # Bestillings-vnr uden match i transaktioner (overhovedet)
    ingen_salg = [
        {"varenummer": vnr, "varenavn": navn}
        for vnr, navn in sorted(bestil_map.items())
        if vnr not in alle_salg
    ]

    # Bestillings-vnr med match i transaktioner seneste 90 dage
    med_salg = [
        {"varenummer": vnr, "varenavn": navn,
         "transaktioner": salg_map[vnr]["transaktioner"],
         "seneste_dato":  salg_map[vnr]["seneste_dato"]}
        for vnr, navn in sorted(bestil_map.items())
        if vnr in salg_map
    ]

    # Salgs-vnr (seneste 90 dage) uden match i nogen bestilling
    kun_salg = [
        {"varenummer": vnr, "varenavn": info["varenavn"],
         "transaktioner": info["transaktioner"],
         "seneste_dato":  info["seneste_dato"]}
        for vnr, info in sorted(salg_map.items())
        if vnr not in bestil_map
    ]

    return {
        "med_salg":   med_salg,
        "ingen_salg": ingen_salg,
        "kun_salg":   kun_salg,
    }


def _stamdata_type_map() -> Dict[str, str]:
    """Returnerer {varenavn.lower(): type} fra varestamdata."""
    with _conn() as conn:
        rows = conn.execute("SELECT varenavn, type FROM varestamdata").fetchall()
    return {r["varenavn"].lower().strip(): r["type"] for r in rows}


def hent_bestillings_uge(maal_uge: int, maal_aar: int) -> Dict:
    """Produktniveau bestillingsanbefaling for mål-uge.

    Basis: senest indlæste ugebestilling før mål-ugen.
    Formel pr. dag: basis_dag × SI × dag_fak × TGTG-korr × (1 + vækst)
    """
    from datetime import date, timedelta
    DAGE = ['man', 'tir', 'ons', 'tor', 'fre', 'loe', 'son']
    TGTG_PR_POSE = 38.0

    with _conn() as conn:
        # ── Har vi en faktisk indlæst bestilling for mål-ugen? ──────────────
        faktisk_rows = conn.execute("""
            SELECT varenummer, varenavn, pris_ex_moms,
                   man, tir, ons, tor, fre, loe, son
            FROM ugebestillinger
            WHERE uge=? AND aar=?
            ORDER BY id
        """, (maal_uge, maal_aar)).fetchall()

        if faktisk_rows:
            mon_dato = date.fromisocalendar(maal_aar, maal_uge, 1)
            sd_map   = _stamdata_type_map()
            produkter = []
            for r in faktisk_rows:
                dag_vals = {d: int(float(r[d] or 0)) for d in DAGE}
                total_p  = sum(dag_vals.values())
                pris     = float(r["pris_ex_moms"] or 0)
                kat      = _kat(r["varenavn"], sd_map)
                produkter.append({
                    "varenummer":      r["varenummer"] or "",
                    "varenavn":        r["varenavn"],
                    "kategori":        kat,
                    "pris_ex_moms":    round(pris, 2),
                    "basis":           dag_vals,
                    "anbefalet":       dag_vals,
                    "manuel":          {},
                    "total_basis":     total_p,
                    "total_anbefalet": total_p,
                    "total_pris":      round(total_p * pris, 2),
                })
            total_stk = sum(p["total_anbefalet"] for p in produkter)
            total_kr  = sum(p["total_pris"]      for p in produkter)
            return {
                "maal_uge":        maal_uge,
                "maal_aar":        maal_aar,
                "dato_range":      _dato_range(maal_uge, maal_aar),
                "basis_uge":       maal_uge,
                "basis_aar":       maal_aar,
                "maaned":          mon_dato.month,
                "si":              1.0,
                "event":           None,
                "tgtg_kr":         0,
                "tgtg_ok":         True,
                "tgtg_advarsel":   False,
                "tgtg_korrektion": 1.0,
                "vaekst_pct":      0.0,
                "total_faktor":    1.0,
                "produkter":       produkter,
                "total_stk":       total_stk,
                "total_kr":        round(total_kr, 2),
                "faktisk":         True,
            }

        # ── Ingen faktisk bestilling → beregn anbefaling ────────────────────
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
    evt = _get_event(maal_uge, maal_aar)
    dag_fak = evt["dag_fak"] if evt else {d: 1.0 for d in DAGE}
    total_faktor = si * (evt["factor"] if evt else 1.0) * tgtg_korr * (1 + vaekst)

    # Byg produkttabel
    sd_map = _stamdata_type_map()
    produkter = []
    for r in prod_rows:
        basis_dag = {d: float(r[d] or 0) for d in DAGE}
        kat = _kat(r["varenavn"], sd_map)
        vn  = r["varenummer"] or ""

        min_anb_dage: set = set()

        if kat == 'Kage':
            anb_dag = {d: int(basis_dag[d]) for d in DAGE}
        else:
            anb_dag = {}
            for d in DAGE:
                b = basis_dag[d]
                if b > 0:
                    raw = b * si * dag_fak.get(d, 1.0) * tgtg_korr * (1 + vaekst)
                    anb_dag[d] = int(round(raw))
                else:
                    d_fak = dag_fak.get(d, 1.0)
                    if evt and d_fak > 1.10:
                        # Basis = 0, men event løfter denne dag markant.
                        # Estimer fra den dag med højest event-faktor der HAR basis > 0.
                        ref_candidates = [
                            (dag_fak.get(rd, 1.0), basis_dag[rd], rd)
                            for rd in DAGE if basis_dag[rd] > 0
                        ]
                        if ref_candidates:
                            ref_fak, ref_b, _ = max(ref_candidates, key=lambda x: x[0])
                            raw_min = ref_b * (d_fak / max(ref_fak, 0.01)) * si * tgtg_korr * (1 + vaekst)
                            anb_dag[d] = max(1, int(round(raw_min)))
                            min_anb_dage.add(d)
                        else:
                            anb_dag[d] = 0
                    else:
                        anb_dag[d] = 0

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
            "min_anb_dage":    list(min_anb_dage),
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
        "faktisk":         False,
    }


# ── VF DRILL-DOWN ─────────────────────────────────────────────────────────────

def hent_vf_detaljer(aar: int, maaned: int) -> Dict:
    """Ugevis bageri-faktura + kategori-niveau andet VF for en enkelt måned."""
    from datetime import date as _date, timedelta as _td
    with _conn() as conn:
        # Hvilke ISO-uger falder (overvejende) i denne måned?
        # Vi bruger uger hvor mandag ligger i måneden.
        first = _date(aar, maaned, 1)
        last  = _date(aar, maaned + 1, 1) - _td(days=1) if maaned < 12 else _date(aar, 12, 31)

        # Find alle ISO-uger hvor mindst én dag er i måneden
        uger_i_maaned = set()
        dag = first
        while dag <= last:
            uger_i_maaned.add((dag.isocalendar()[0], dag.isocalendar()[1]))
            dag += _td(days=7)
        # Inkludér ugen for første dag og sidste dag
        uger_i_maaned.add(first.isocalendar()[:2])
        uger_i_maaned.add(last.isocalendar()[:2])

        # Hent bager_regnskab for disse uger
        bager_rækker = []
        for (y, w) in sorted(uger_i_maaned):
            row = conn.execute("""
                SELECT b.uge, b.aar, b.faktura, b.retur_ialt,
                       COALESCE(u.bestilt_kr, 0) AS bestilt_kr
                FROM bager_regnskab b
                LEFT JOIN (
                    SELECT uge, aar, ROUND(SUM(total_pris),2) AS bestilt_kr
                    FROM ugebestillinger GROUP BY uge, aar
                ) u ON u.uge = b.uge AND u.aar = b.aar
                WHERE b.uge=? AND b.aar=?
            """, (w, y)).fetchone()
            if row and (row["faktura"] or 0) > 0:
                bager_rækker.append(dict(row))

        # Andet VF per kategori for måneden (Shopbox kostpris, non-bager)
        andet_rows = conn.execute("""
            SELECT
                CASE WHEN kategori != '' THEN kategori ELSE 'Øvrige' END AS kategori,
                ROUND(SUM(kostpris), 0) AS vf
            FROM transaktioner
            WHERE strftime('%Y', dato) = ?
              AND CAST(strftime('%m', dato) AS INTEGER) = ?
              AND CAST(CAST(varenummer AS REAL) AS INTEGER) NOT IN (
                  SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                  FROM ugebestillinger WHERE varenummer != '' AND varenummer != '0'
              )
            GROUP BY kategori
            ORDER BY vf DESC
        """, (str(aar), maaned)).fetchall()

    return {
        "aar":        aar,
        "maaned":     maaned,
        "bager_vf":   bager_rækker,
        "andet_vf":   [dict(r) for r in andet_rows],
    }


# ── FASTE OMKOSTNINGER ────────────────────────────────────────────────────────

def hent_faste_omk(aar: int) -> List[Dict]:
    """Returnerer alle faste omkostnings-rækker for et år."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT id, aar, maaned, kategori, beloeb
            FROM faste_omkostninger
            WHERE aar = ?
            ORDER BY kategori, maaned
        """, (aar,)).fetchall()
        return [dict(r) for r in rows]


def gem_faste_omk(aar: int, maaned: int, kategori: str, beloeb: float) -> None:
    """Upsert én celle (aar, maaned, kategori) → beloeb."""
    with _conn() as conn:
        conn.execute("""
            INSERT INTO faste_omkostninger (aar, maaned, kategori, beloeb)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(aar, maaned, kategori) DO UPDATE SET beloeb = excluded.beloeb
        """, (aar, maaned, kategori.strip(), round(beloeb, 2)))


def slet_faste_omk_kategori(aar: int, kategori: str) -> None:
    """Sletter alle rækker for en hel kategori i et givent år."""
    with _conn() as conn:
        conn.execute(
            "DELETE FROM faste_omkostninger WHERE aar=? AND kategori=?",
            (aar, kategori.strip())
        )


def faste_omk_maaned_sum(aar: int) -> Dict[int, float]:
    """Returnerer {maaned: sum_beloeb} for alle kategorier i et år."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT maaned, SUM(beloeb) AS total
            FROM faste_omkostninger
            WHERE aar = ?
            GROUP BY maaned
        """, (aar,)).fetchall()
        return {r["maaned"]: r["total"] for r in rows}
