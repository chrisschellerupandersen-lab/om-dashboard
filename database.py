import sqlite3
import os
import math
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
        # Migration: varekostpris skal være unik per (varenummer, varenavn, gyldig_fra)
        # — delte varenumre (fx "Romkugle" + "3 X Romkugler" begge på sku 10078)
        # overskrev ellers hinandens kostpris ved hver import.
        _vk = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='varekostpris'"
        ).fetchone()
        if _vk and "UNIQUE(varenummer, gyldig_fra)" in (_vk["sql"] or ""):
            conn.executescript("""
                DROP VIEW IF EXISTS v_transaktioner;
                ALTER TABLE varekostpris RENAME TO varekostpris_gl;
                CREATE TABLE varekostpris (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    varenummer     TEXT    NOT NULL,
                    varenavn       TEXT    NOT NULL DEFAULT '',
                    kostpris_enhed REAL    NOT NULL DEFAULT 0,
                    gyldig_fra     TEXT    NOT NULL,
                    gyldig_til     TEXT,
                    kilde          TEXT    DEFAULT 'auto',
                    opdateret      TEXT    DEFAULT (datetime('now','localtime')),
                    UNIQUE(varenummer, varenavn, gyldig_fra) ON CONFLICT REPLACE
                );
                INSERT INTO varekostpris (id, varenummer, varenavn, kostpris_enhed, gyldig_fra, gyldig_til, kilde, opdateret)
                    SELECT id, varenummer, varenavn, kostpris_enhed, gyldig_fra, gyldig_til, kilde, opdateret
                    FROM varekostpris_gl;
                DROP TABLE varekostpris_gl;
            """)
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
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id           TEXT    DEFAULT '',
                navn              TEXT    NOT NULL,
                kreditpris        REAL    NOT NULL DEFAULT 0,
                kostpris_pose     REAL    DEFAULT 0,
                enheder_per_pose  INTEGER DEFAULT 1,
                aktiv             INTEGER DEFAULT 1,
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

            CREATE TABLE IF NOT EXISTS basis_bestilling (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                varenummer      TEXT    NOT NULL,
                varenavn        TEXT    NOT NULL,
                dag             TEXT    NOT NULL,
                anbefalet_antal INTEGER NOT NULL DEFAULT 0,
                kategori        TEXT    DEFAULT '',
                opdateret       TEXT    DEFAULT (datetime('now','localtime')),
                UNIQUE(varenummer, dag) ON CONFLICT REPLACE
            );
            CREATE INDEX IF NOT EXISTS idx_basis_vare ON basis_bestilling(varenummer);
            CREATE INDEX IF NOT EXISTS idx_basis_dag ON basis_bestilling(dag);

            CREATE TABLE IF NOT EXISTS mobilepay (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                aar        INTEGER NOT NULL,
                maaned     INTEGER NOT NULL,
                omsaetning REAL    NOT NULL DEFAULT 0,
                UNIQUE(aar, maaned) ON CONFLICT REPLACE
            );

            CREATE TABLE IF NOT EXISTS mobilepay_dag (
                dato              TEXT    PRIMARY KEY,
                omsaetning_netto  REAL    NOT NULL DEFAULT 0,
                gebyr             REAL    NOT NULL DEFAULT 0,
                omsaetning_inkl   REAL    NOT NULL DEFAULT 0,
                kilde             TEXT    DEFAULT 'api',
                indlæst           TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS retur_detaljer (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                registreret_dato TEXT    NOT NULL,
                uge              INTEGER NOT NULL,
                aar              INTEGER NOT NULL,
                produkt          TEXT    NOT NULL,
                antal            INTEGER NOT NULL DEFAULT 0,
                kategori         TEXT    NOT NULL DEFAULT 'wienerbroed'
            );
            CREATE INDEX IF NOT EXISTS idx_retur_uge ON retur_detaljer(uge, aar);

            CREATE TABLE IF NOT EXISTS management_review (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                genereret_dato  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                model           TEXT    DEFAULT 'claude',
                data_snapshot   TEXT,
                indhold_json    TEXT    NOT NULL
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

            CREATE TABLE IF NOT EXISTS helligdage (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                dato     TEXT    NOT NULL UNIQUE,
                navn     TEXT    NOT NULL,
                type     TEXT    DEFAULT 'normal'
            );

            CREATE TABLE IF NOT EXISTS gmail_importerede (
                msg_id     TEXT PRIMARY KEY,
                uge        INTEGER,
                aar        INTEGER,
                importeret TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS gmail_sync_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                tidspunkt TEXT DEFAULT (datetime('now','localtime')),
                status    TEXT,
                besked    TEXT,
                antal     INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS varekostpris (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                varenummer     TEXT    NOT NULL,
                varenavn       TEXT    NOT NULL DEFAULT '',
                kostpris_enhed REAL    NOT NULL DEFAULT 0,
                gyldig_fra     TEXT    NOT NULL,
                gyldig_til     TEXT,
                kilde          TEXT    DEFAULT 'auto',
                opdateret      TEXT    DEFAULT (datetime('now','localtime')),
                UNIQUE(varenummer, varenavn, gyldig_fra) ON CONFLICT REPLACE
            );
            CREATE INDEX IF NOT EXISTS idx_kp_vn ON varekostpris(varenummer, gyldig_fra);

            DROP VIEW IF EXISTS v_transaktioner;
            CREATE VIEW v_transaktioner AS
            WITH bon_has_zero AS (
                SELECT dato, bon_nr
                FROM transaktioner
                WHERE bon_nr != '' AND omsætning <= 0 AND kostpris > 0
                GROUP BY dato, bon_nr
            ),
            bon_totals AS (
                SELECT t.dato, t.bon_nr,
                       SUM(t.omsætning) AS bon_oms,
                       SUM(t.kostpris)  AS bon_kost
                FROM transaktioner t
                INNER JOIN bon_has_zero b ON t.dato = b.dato AND t.bon_nr = b.bon_nr
                GROUP BY t.dato, t.bon_nr
            ),
            t_korr AS (
                SELECT t.*,
                       CASE
                           WHEN bt.bon_nr IS NOT NULL AND bt.bon_kost > 0 AND bt.bon_oms > 0
                           THEN bt.bon_oms * t.kostpris / bt.bon_kost
                           ELSE t.omsætning
                       END AS omsætning_korr
                FROM transaktioner t
                LEFT JOIN bon_totals bt ON t.dato = bt.dato AND t.bon_nr = bt.bon_nr
            )
            SELECT tc.*,
                   tc.omsætning_korr / 1.25 AS omsaetning_ex_moms,
                   CASE
                       WHEN s.pris_ex_moms > 0
                           THEN tc.antal * s.pris_ex_moms / COALESCE(NULLIF(s.portioner,0), 1)
                       WHEN kp.kostpris_enhed IS NOT NULL AND kp.kostpris_enhed > 0 AND tc.antal > 0
                           THEN ROUND(tc.antal * kp.kostpris_enhed, 4)
                       ELSE tc.kostpris
                   END AS vf_korrekt,
                   tc.omsætning_korr / 1.25
                       - CASE
                             WHEN s.pris_ex_moms > 0
                                 THEN tc.antal * s.pris_ex_moms / COALESCE(NULLIF(s.portioner,0), 1)
                             WHEN kp.kostpris_enhed IS NOT NULL AND kp.kostpris_enhed > 0 AND tc.antal > 0
                                 THEN ROUND(tc.antal * kp.kostpris_enhed, 4)
                             ELSE tc.kostpris
                         END AS db_korrekt
            FROM t_korr tc
            LEFT JOIN varestamdata s
                ON (tc.varenummer != '' AND tc.varenummer IS NOT NULL AND tc.varenummer = s.sku)
                OR (COALESCE(tc.varenummer,'') = '' AND LOWER(TRIM(tc.varenavn)) = LOWER(TRIM(s.varenavn)))
            LEFT JOIN varekostpris kp
                ON tc.varenummer != ''
                AND tc.varenummer = kp.varenummer
                AND LOWER(TRIM(kp.varenavn)) = LOWER(TRIM(tc.varenavn))
                AND kp.gyldig_fra <= tc.dato
                AND (kp.gyldig_til IS NULL OR kp.gyldig_til >= tc.dato);
        """)
        # Migrationer til eksisterende tabeller
        for sql in [
            "ALTER TABLE transaktioner ADD COLUMN time_start INTEGER DEFAULT -1",
            "ALTER TABLE transaktioner ADD COLUMN bon_nr TEXT DEFAULT ''",
            "ALTER TABLE ugebestillinger ADD COLUMN sektion INTEGER DEFAULT 1",
            "ALTER TABLE varestamdata ADD COLUMN portioner INTEGER DEFAULT 1",
            "ALTER TABLE tgtg_poser ADD COLUMN kostpris_pose REAL DEFAULT 0",
            "ALTER TABLE tgtg_poser ADD COLUMN enheder_per_pose INTEGER DEFAULT 1",
            "ALTER TABLE bager_regnskab ADD COLUMN faktura REAL DEFAULT 0",
            "ALTER TABLE mobilepay_dag ADD COLUMN omsaetning_netto REAL DEFAULT 0",
            "ALTER TABLE mobilepay_dag ADD COLUMN gebyr REAL DEFAULT 0",
        ]:
            try:
                conn.execute(sql)
            except Exception:
                pass  # kolonnen eksisterer allerede
        # Oprydning MobilePay: tøm gammel data med forkert struktur
        # (før migration til omsaetning_netto + gebyr)
        # Tjek om tabellen har nogle rækker uden omsaetning_netto sat (gammelt format)
        try:
            result = conn.execute(
                "SELECT COUNT(*) FROM mobilepay_dag WHERE omsaetning_netto = 0 AND omsaetning_inkl > 0"
            ).fetchone()
            if result and result[0] > 0:
                # Nogle rækker har gammelt format → slet alt og start fresh
                conn.execute("DELETE FROM mobilepay_dag")
        except Exception:
            pass  # Hvis noget går galt, fortsæt uden at slette

        # Oprydning: slet alle "ØKO - " stamdata-rækker importeret fra bestilling
        # LOWER() i SQLite håndterer ikke Ø → brug UPPER() i stedet
        conn.execute("DELETE FROM varestamdata WHERE UPPER(SUBSTR(varenavn,1,6)) = 'ØKO - '")
        # Slet SKU-duplikater: behold nyeste (højeste id) per sku når sku != ''
        conn.execute("""
            DELETE FROM varestamdata
            WHERE sku != '' AND id NOT IN (
                SELECT MAX(id) FROM varestamdata WHERE sku != '' GROUP BY sku
            )
        """)

        # Fix: ret sektion direkte fra varenavn-regler (kører ved hver opstart)
        # Wienerbrød-varer der fejlagtigt fik sektion=2 eller 4 → 3
        conn.execute("""
            UPDATE ugebestillinger SET sektion=3
            WHERE LOWER(varenavn) LIKE '%tebirkes%'
               OR LOWER(varenavn) LIKE '%grovbirkes%'
               OR LOWER(varenavn) LIKE '%fastelavns%'
               OR LOWER(varenavn) LIKE '%croissant%'
               OR LOWER(varenavn) LIKE '%snegl%'
               OR LOWER(varenavn) LIKE '%snurrer%'
               OR LOWER(varenavn) LIKE '%frøsnapper%'
               OR LOWER(varenavn) LIKE '%spandauer%'
               OR LOWER(varenavn) LIKE '%wienerbr%'
               OR LOWER(varenavn) LIKE '%wienerstang%'
               OR LOWER(varenavn) LIKE '%kanelstang%'
        """)
        # Kager (varenavn-baseret, da bagerens varenumre ≠ Shopbox SKU'er) → sektion 4
        conn.execute("""
            UPDATE ugebestillinger SET sektion=4
            WHERE LOWER(varenavn) LIKE '%kage%'
               OR LOWER(varenavn) LIKE '%cookie%'
               OR LOWER(varenavn) LIKE '%muffin%'
               OR LOWER(varenavn) LIKE '%brownie%'
               OR LOWER(varenavn) LIKE '%romkugl%'
               OR LOWER(varenavn) LIKE '%kokostoppe%'
               OR LOWER(varenavn) LIKE '%napoleonshat%'
               OR LOWER(varenavn) LIKE '%studenterbr%'
               OR LOWER(varenavn) LIKE '%snitter%'
               OR LOWER(varenavn) LIKE '%stammer%'
               OR LOWER(varenavn) LIKE '%honningbomb%'
               OR LOWER(varenavn) LIKE '%honninghjerter%'
        """)

        # Oprydning: slet dubletter i retur_detaljer — samme registreret_dato med forskellig uge
        # Beholder poster med korrekt ISO-uge (beregnet fra dato), sletter forkerte
        conn.execute("""
            DELETE FROM retur_detaljer
            WHERE id NOT IN (
                SELECT MIN(id) FROM retur_detaljer GROUP BY registreret_dato, produkt, kategori
            )
        """)

        # Seed: sæt korrekte værdier på kendte TGTG-pose-typer
        # Kører altid så eksisterende rækker opdateres uden at vente på næste sync
        _TGTG_SEED = [
            # (item_id, enheder_per_pose, kreditpris)
            ("206880476083086176", 6, 67.75),  # Lykkepose
            ("206881838829236480", 5, 41.22),  # Brødposen
            ("206882511213524800", 6, 60.12),  # Wienerbrødsposen
            ("210383102918979712", 4, 40.00),  # 4x Fatelavnsboller
            ("210383866617850400", 6, 50.13),  # Kagepose
        ]
        for item_id, enheder, kreditpris in _TGTG_SEED:
            conn.execute(
                "UPDATE tgtg_poser SET enheder_per_pose=?, kreditpris=? WHERE item_id=?",
                (enheder, kreditpris, item_id)
            )


def _opdater_kostpris_historik(conn, transaktioner: List[Dict], import_dato: str) -> None:
    """Sammenlign nye priser med gemt historik og opret poster automatisk ved ændringer.
    Nøglet på (varenummer, varenavn) — flere varer kan dele samme varenummer
    (fx single-vare + multipak)."""
    from collections import defaultdict
    priser: Dict = defaultdict(list)
    for t in transaktioner:
        vn   = str(t.get("varenummer", "") or "").strip()
        navn = str(t.get("varenavn", "") or "").strip()
        if not vn or vn in ("0", ""):
            continue
        antal = float(t.get("antal", 0) or 0)
        kost  = float(t.get("kostpris", 0) or 0)
        if antal > 0 and kost > 0:
            priser[(vn, navn)].append(round(kost / antal, 6))

    for (vn, navn), pris_liste in priser.items():
        sorted_p  = sorted(pris_liste)
        median_p  = round(sorted_p[len(sorted_p) // 2], 4)
        if median_p <= 0:
            continue

        aktuel = conn.execute(
            "SELECT id, kostpris_enhed FROM varekostpris "
            "WHERE varenummer=? AND LOWER(TRIM(varenavn))=LOWER(TRIM(?)) AND gyldig_til IS NULL",
            (vn, navn)
        ).fetchone()

        if aktuel is None:
            # Første gang — opret startpost
            conn.execute(
                "INSERT OR IGNORE INTO varekostpris (varenummer, varenavn, kostpris_enhed, gyldig_fra, kilde) VALUES (?,?,?,?,'auto')",
                (vn, navn, median_p, import_dato)
            )
        else:
            gammel = float(aktuel["kostpris_enhed"] or 0)
            if gammel > 0 and abs(median_p - gammel) / gammel > 0.02:  # >2% ændring
                from datetime import date as _d, timedelta as _td
                gaeldende_til = (_d.fromisoformat(import_dato) - _td(days=1)).isoformat()
                conn.execute(
                    "UPDATE varekostpris SET gyldig_til=? WHERE id=?",
                    (gaeldende_til, aktuel["id"])
                )
                conn.execute(
                    "INSERT OR IGNORE INTO varekostpris (varenummer, varenavn, kostpris_enhed, gyldig_fra, kilde) VALUES (?,?,?,?,'auto')",
                    (vn, navn, median_p, import_dato)
                )


def gem_transaktioner(rapport_dato: str, transaktioner: List[Dict]) -> int:
    with _conn() as conn:
        # Opdater kostpris-historik FØR sletning — bevar historisk korrekthed
        _opdater_kostpris_historik(conn, transaktioner, rapport_dato)
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

def _fair_periode_where(start: str, slut_dag: str, seneste_time) -> tuple:
    """WHERE-fragment til fair sammenligning med en IGANGVÆRENDE periode.

    Tæller fulde dage før slut_dag, men kun transaktioner op til samme tidspunkt
    (time_start <= seneste_time) PÅ slut_dag. Så fx 'forrige uge' sammenlignes mod
    samme ugedag OG samme tidspunkt på dagen — ikke en hel dag mod en halv.

    Historiske rækker uden registreret time (time_start = -1) tælles altid med,
    da -1 <= seneste_time. Returnerer (sql_fragment, params).
    """
    if seneste_time is None:
        return "(dato >= ? AND dato <= ?)", (start, slut_dag)
    return ("((dato >= ? AND dato < ?) OR (dato = ? AND time_start <= ?))",
            (start, slut_dag, slut_dag, seneste_time))


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

        # Beregn ISO-ugens mandag og "forrige uge samme periode"
        from datetime import date as _kpi_date, timedelta as _kpi_td
        _sd        = _kpi_date.fromisoformat(seneste_dato)
        _uge_man   = _sd - _kpi_td(days=_sd.weekday())   # mandag i indeværende ISO-uge
        _prev_man  = _uge_man  - _kpi_td(days=7)         # mandag forrige uge
        _prev_end  = _sd       - _kpi_td(days=7)         # samme ugedag forrige uge
        uge_mandag = _uge_man.isoformat()
        prev_uge_start = _prev_man.isoformat()
        prev_uge_end   = _prev_end.isoformat()

        # Seneste registrerede time på seneste dag — markerer "hvor langt inde i dagen"
        # vi er. Bruges til fair sammenligning: igangværende periode måles kun mod
        # samme TIDSPUNKT i tidligere perioder, ikke mod hele dage/uger.
        seneste_time = conn.execute("""
            SELECT MAX(time_start) FROM transaktioner
            WHERE dato = ? AND time_start >= 0
        """, (seneste_dato,)).fetchone()[0]

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
                   CASE WHEN COUNT(CASE WHEN bon_nr != '' THEN 1 END) > 0
                        THEN COUNT(DISTINCT CASE WHEN bon_nr != '' THEN bon_nr END)
                        ELSE COUNT(*)
                   END                           AS transak,
                   COUNT(DISTINCT dato)          AS antal_dage
            FROM v_transaktioner
            WHERE dato >= ? AND dato <= ?
        """, (uge_mandag, seneste_dato)).fetchone()

        # Ugesnit: udeluk indeværende uge (delvis) og brug kun afsluttede uger
        snit_where = f"WHERE strftime('%Y', dato) = '{aar}' AND dato < ?" if aar else "WHERE dato < ?"
        snit_row = conn.execute(f"""
            SELECT AVG(uge_total) AS snit_uge FROM (
                SELECT SUM(omsætning) AS uge_total
                FROM transaktioner
                {snit_where}
                GROUP BY strftime('%Y-%W', dato)
                ORDER BY dato DESC LIMIT 12
            )
        """, (uge_mandag,) if not aar else (uge_mandag,)).fetchone()

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

        # Forrige uge — SAMME periode som indeværende (mandag til samme ugedag)
        # Fx tirsdag uge 21 kl.14 → sammenlignes mod mandag(fuld)+tirsdag(til kl.14) uge 20
        _pu_where, _pu_params = _fair_periode_where(prev_uge_start, prev_uge_end, seneste_time)
        prev_uge_row = conn.execute(f"""
            SELECT COALESCE(SUM(omsætning),0)  AS omsaetning,
                   COALESCE(SUM(db_korrekt),0) AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                        ELSE 0 END             AS db_pct,
                   CASE WHEN COUNT(CASE WHEN bon_nr != '' THEN 1 END) > 0
                        THEN COUNT(DISTINCT CASE WHEN bon_nr != '' THEN bon_nr END)
                        ELSE COUNT(*)
                   END                         AS transak,
                   COUNT(DISTINCT dato)        AS antal_dage
            FROM v_transaktioner
            WHERE {_pu_where}
        """, _pu_params).fetchone()

        # Samme dag forrige uge — kun op til samme time som i dag
        prev_dag_dato = conn.execute(
            "SELECT date(?, '-7 days')", (seneste_dato,)
        ).fetchone()[0]
        time_filter = "AND time_start <= ?" if seneste_time is not None else ""
        time_params = (seneste_time,) if seneste_time is not None else ()
        prev_dag_row = conn.execute(f"""
            SELECT COALESCE(SUM(omsætning),0)  AS omsaetning,
                   COALESCE(SUM(db_korrekt),0) AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                        ELSE 0 END             AS db_pct,
                   CASE WHEN COUNT(CASE WHEN bon_nr != '' THEN 1 END) > 0
                        THEN COUNT(DISTINCT CASE WHEN bon_nr != '' THEN bon_nr END)
                        ELSE COUNT(*)
                   END                         AS transak,
                   MAX(time_start)             AS til_time
            FROM v_transaktioner WHERE dato = ? {time_filter}
        """, (prev_dag_dato,) + time_params).fetchone()

        # Samme dag 2 uger siden (seneste_dato - 14 dage) — samme time-cutoff
        prev_prev_dag_dato = conn.execute(
            "SELECT date(?, '-7 days')", (prev_dag_dato,)
        ).fetchone()[0]
        prev_prev_dag_row = conn.execute(f"""
            SELECT COALESCE(SUM(omsætning),0)  AS omsaetning,
                   COALESCE(SUM(db_korrekt),0) AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                        ELSE 0 END             AS db_pct
            FROM v_transaktioner WHERE dato = ? {time_filter}
        """, (prev_prev_dag_dato,) + time_params).fetchone()

        # 12-ugers snit — de 12 seneste samme ugedage, samme time-cutoff
        # Beregnes direkte som datoer (undgår ORDER BY LIMIT i subquery)
        prev_4_dage = [(_sd - _kpi_td(days=7*(i+1))).isoformat() for i in range(12)]
        _ph4 = ','.join(['?' for _ in prev_4_dage])
        snit_4u_time_filter = "AND time_start <= ?" if seneste_time is not None else ""
        snit_4u_time_params = (seneste_time,) if seneste_time is not None else ()
        snit_4u_row = conn.execute(f"""
            SELECT AVG(dag_omsat)   AS snit_omsaetning,
                   AVG(dag_transak) AS snit_transak
            FROM (
                SELECT SUM(omsætning) AS dag_omsat,
                       CASE WHEN COUNT(CASE WHEN bon_nr != '' THEN 1 END) > 0
                            THEN COUNT(DISTINCT CASE WHEN bon_nr != '' THEN bon_nr END)
                            ELSE COUNT(*) END AS dag_transak
                FROM v_transaktioner
                WHERE dato IN ({_ph4})
                  {snit_4u_time_filter}
                GROUP BY dato
            )
        """, tuple(prev_4_dage) + snit_4u_time_params).fetchone()

        # MTD: fra 1. i indeværende måned til seneste dag
        mtd_start = seneste_dato[:8] + '01'  # YYYY-MM-01
        mtd_row = conn.execute("""
            SELECT COALESCE(SUM(omsætning),0)  AS omsaetning,
                   COALESCE(SUM(db_korrekt),0) AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                        ELSE 0 END             AS db_pct,
                   CASE WHEN COUNT(CASE WHEN bon_nr != '' THEN 1 END) > 0
                        THEN COUNT(DISTINCT CASE WHEN bon_nr != '' THEN bon_nr END)
                        ELSE COUNT(*)
                   END                         AS transak,
                   COUNT(DISTINCT dato)        AS antal_dage
            FROM v_transaktioner WHERE dato >= ? AND dato <= ?
        """, (mtd_start, seneste_dato)).fetchone()

        # Forrige måned – samme periode (1. til dato -1 måned), sidste dag time-capped
        prev_mtd_start = conn.execute(
            "SELECT date(?, '-1 month')", (mtd_start,)
        ).fetchone()[0]
        prev_mtd_end = conn.execute(
            "SELECT date(?, '-1 month')", (seneste_dato,)
        ).fetchone()[0]
        _pm_where, _pm_params = _fair_periode_where(prev_mtd_start, prev_mtd_end, seneste_time)
        prev_mtd_row = conn.execute(f"""
            SELECT COALESCE(SUM(omsætning),0)  AS omsaetning,
                   COALESCE(SUM(db_korrekt),0) AS db_kr,
                   CASE WHEN SUM(omsætning)>0
                        THEN SUM(db_korrekt)*1.25/SUM(omsætning)*100
                        ELSE 0 END             AS db_pct
            FROM v_transaktioner WHERE {_pm_where}
        """, _pm_params).fetchone()

        # Retur boller + wienerbrød for indeværende ISO-uge
        iso = _sd.isocalendar()
        bager_uge_row = conn.execute("""
            SELECT retur_wiener, retur_boller, retur_ialt, tgtg, faktura
            FROM bager_regnskab WHERE uge = ? AND aar = ?
        """, (iso[1], iso[0])).fetchone()

        # Bestillingsdata for ugen: antal og kr per kategori (wiener / boller)
        _W = ("LOWER(varenavn) LIKE '%wiener%' OR LOWER(varenavn) LIKE '%croissant%'"
              " OR LOWER(varenavn) LIKE '%crossaint%'"
              " OR LOWER(varenavn) LIKE '%snegl%' OR LOWER(varenavn) LIKE '%snurrer%'"
              " OR LOWER(varenavn) LIKE '%tebirkes%' OR LOWER(varenavn) LIKE '%grovbirkes%'"
              " OR LOWER(varenavn) LIKE '%spandauer%' OR LOWER(varenavn) LIKE '%kanelstang%'"
              " OR LOWER(varenavn) LIKE '%frøsnapper%'")
        _B = ("LOWER(varenavn) LIKE '%bolle%' OR LOWER(varenavn) LIKE '%hveder%'"
              " OR LOWER(varenavn) LIKE '%musli%' OR LOWER(varenavn) LIKE '%teboller%'")
        bestil_wien = conn.execute(f"""
            SELECT SUM(man+tir+ons+tor+fre+loe+son) AS stk,
                   SUM(total_pris) AS kr
            FROM ugebestillinger WHERE uge=? AND aar=? AND ({_W})
        """, (iso[1], iso[0])).fetchone()
        bestil_boller = conn.execute(f"""
            SELECT SUM(man+tir+ons+tor+fre+loe+son) AS stk,
                   SUM(total_pris) AS kr
            FROM ugebestillinger WHERE uge=? AND aar=? AND ({_B})
        """, (iso[1], iso[0])).fetchone()

        def _retur_stk(retur_kr, bestil_stk, bestil_kr):
            """Beregn antal returneret fra kr-beløb og pris/stk."""
            if not retur_kr or not bestil_stk or not bestil_kr or bestil_kr == 0:
                return None
            pris_per_stk = bestil_kr / bestil_stk
            return round(retur_kr / pris_per_stk)

        w_stk = int(bestil_wien["stk"])  if (bestil_wien and bestil_wien["stk"]) else None
        w_kr  = bestil_wien["kr"]        if (bestil_wien and bestil_wien["kr"])  else None
        b_stk = int(bestil_boller["stk"]) if (bestil_boller and bestil_boller["stk"]) else None
        b_kr  = bestil_boller["kr"]       if (bestil_boller and bestil_boller["kr"])  else None

        # Altid vis retur-prognose baseret på bestilling (10% boller, 13,5% wiener)
        # Hvis faktura for ugen findes, brug faktiske retur-kr til at beregne stk
        RETUR_BOLLER = 0.10
        RETUR_WIENER = 0.135
        bager_retur_info = None
        if w_stk or b_stk:
            if bager_uge_row and (bager_uge_row["retur_wiener"] or bager_uge_row["retur_boller"]):
                # Faktiske retur fra faktura
                wien_stk   = _retur_stk(bager_uge_row["retur_wiener"], w_stk, w_kr)
                boller_stk = _retur_stk(bager_uge_row["retur_boller"], b_stk, b_kr)
                wien_kr    = bager_uge_row["retur_wiener"]
                boller_kr  = bager_uge_row["retur_boller"]
                kilde      = "faktura"
            else:
                # Prognose: 10% boller, 13,5% wiener
                wien_stk   = round(w_stk * RETUR_WIENER) if w_stk else None
                boller_stk = round(b_stk * RETUR_BOLLER) if b_stk else None
                wien_kr    = round(w_kr * RETUR_WIENER, 2) if w_kr else None
                boller_kr  = round(b_kr * RETUR_BOLLER, 2) if b_kr else None
                kilde      = "prognose"
            bager_retur_info = {
                "aktuel_uge":         iso[1],
                "wien_retur_stk":     wien_stk,
                "wien_bestilt_stk":   w_stk,
                "wien_retur_kr":      wien_kr,
                "boller_retur_stk":   boller_stk,
                "boller_bestilt_stk": b_stk,
                "boller_retur_kr":    boller_kr,
                "retur_ialt": bager_uge_row["retur_ialt"] if bager_uge_row else None,
                "kilde": kilde,
            }

    return {
        "dag":              dict(dag)               if dag               else None,
        "uge":              dict(uge)               if uge               else None,
        "uge_mandag":       uge_mandag,
        "prev_uge":         dict(prev_uge_row)      if prev_uge_row      else None,
        "prev_uge_start":   prev_uge_start,
        "prev_uge_end":     prev_uge_end,
        "prev_dag":         dict(prev_dag_row)      if prev_dag_row      else None,
        "prev_dag_dato":    prev_dag_dato,
        "seneste_time":     seneste_time,
        "prev_prev_dag":    dict(prev_prev_dag_row) if prev_prev_dag_row else None,
        "mtd":              dict(mtd_row)           if mtd_row           else None,
        "prev_mtd":         dict(prev_mtd_row)      if prev_mtd_row      else None,
        "snit_uge":         snit_row["snit_uge"]    if snit_row          else None,
        "snit_dag":         dag_snit_row["snit_dag"] if dag_snit_row     else None,
        "snit_12uger_dag":  dict(snit_4u_row)        if snit_4u_row       else None,
        "bager_uge":        dict(bager_uge_row)     if bager_uge_row     else None,
        "bager_retur":      bager_retur_info,
        "bager_iso_uge":    iso[1],
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


def hent_dag_produkter_by_date(dato: str, aar: int = None) -> Dict:
    """Produkter solgt på specificeret dag, sorteret efter omsætning."""
    with _conn() as conn:
        # Valider dato format (YYYY-MM-DD)
        try:
            from datetime import datetime
            datetime.strptime(dato, '%Y-%m-%d')
        except ValueError:
            return {"dato": None, "produkter": []}

        # Kontroller at dato eksisterer i databasen
        exists = conn.execute(
            "SELECT COUNT(*) FROM transaktioner WHERE dato = ?",
            (dato,)
        ).fetchone()[0]

        if not exists:
            return {"dato": dato, "produkter": []}

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
        """, (dato,)).fetchall()
    return {"dato": dato, "produkter": [dict(r) for r in rows]}


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


# Cache til dag-fordelingsnøgle (beregnes én gang per process)
_DAG_NØGLE_CACHE: Optional[List[float]] = None

def _dag_fordeling_nøgle() -> List[float]:
    """Returnerer fordelingsnøgle [man, tir, ons, tor, fre, loe, son] som andele (sum=1.0).
    Beregnet fra gennemsnitlig dagsomsætning i transaktioner.
    Fallback: uniform 1/7 hvis ingen data."""
    global _DAG_NØGLE_CACHE
    if _DAG_NØGLE_CACHE is not None:
        return _DAG_NØGLE_CACHE
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    -- strftime %w: 0=søn, 1=man, 2=tir, 3=ons, 4=tor, 5=fre, 6=loe
                    CAST(strftime('%w', dato) AS INTEGER) AS dow,
                    AVG(dag_oms) AS snit
                FROM (
                    SELECT dato, SUM(omsætning) AS dag_oms
                    FROM transaktioner
                    GROUP BY dato
                )
                GROUP BY dow
            """).fetchall()
        # Byg liste [man, tir, ons, tor, fre, loe, son] (Python weekday: 0=man)
        # SQLite %w: 0=søn=6, 1=man=0, 2=tir=1, 3=ons=2, 4=tor=3, 5=fre=4, 6=loe=5
        dow_map = {r["dow"]: r["snit"] or 0.0 for r in rows}
        sqlite_til_python = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 0: 6}
        snit = [dow_map.get(sqlite_dow, 0.0) for sqlite_dow, _ in
                sorted(sqlite_til_python.items(), key=lambda x: x[1])]
        total = sum(snit)
        if total > 0:
            nøgle = [s / total for s in snit]
        else:
            nøgle = [1.0 / 7.0] * 7
    except Exception:
        nøgle = [1.0 / 7.0] * 7
    _DAG_NØGLE_CACHE = nøgle
    return nøgle


def _mp_map_alle() -> Dict:
    """Returnerer {(aar, maaned): omsaetning_inkl_moms}.
    Foretrækker daglig data (mobilepay_dag) over manuelle månedstotaler."""
    from datetime import date as _d
    with _conn() as conn:
        # Daglig data → aggregér til måneder
        dag_rows = conn.execute(
            "SELECT dato, omsaetning_inkl FROM mobilepay_dag"
        ).fetchall()
        maaned_map: Dict = {}
        for r in dag_rows:
            dt = _d.fromisoformat(r["dato"])
            key = (dt.year, dt.month)
            maaned_map[key] = maaned_map.get(key, 0.0) + r["omsaetning_inkl"]
        # Manuel månedstotal som fallback for måneder uden daglig data
        mnd_rows = conn.execute("SELECT aar, maaned, omsaetning FROM mobilepay").fetchall()
        for r in mnd_rows:
            key = (r["aar"], r["maaned"])
            if key not in maaned_map:
                maaned_map[key] = r["omsaetning"]
    return maaned_map


def _mp_dag_map(fra_dato: str, til_dato: str) -> Dict:
    """Returnerer {dato_str: omsaetning_inkl} for daglig MP-data i perioden."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT dato, omsaetning_inkl FROM mobilepay_dag WHERE dato BETWEEN ? AND ?",
            (fra_dato, til_dato)
        ).fetchall()
    return {r["dato"]: r["omsaetning_inkl"] for r in rows}


def _mp_uge_netto(iso_aar: int, iso_uge: int) -> float:
    """Returner MobilePay netto (÷1.25) for en ISO-uge.
    Bruger daglig data hvis tilgængelig, ellers pro-rater månedstotal."""
    from datetime import date as _d, timedelta as _td
    from calendar import monthrange as _mr
    mandag = _d.fromisocalendar(iso_aar, iso_uge, 1)
    sondag = mandag + _td(days=6)

    with _conn() as conn:
        # Check om der er daglig data for nogen dag i ugen
        dag_rows = conn.execute(
            "SELECT dato, omsaetning_inkl FROM mobilepay_dag WHERE dato BETWEEN ? AND ?",
            (mandag.isoformat(), sondag.isoformat())
        ).fetchall()

    if dag_rows:
        # Eksakt: sum af faktiske dage i ugen
        total_inkl = sum(r["omsaetning_inkl"] for r in dag_rows)
        return round(total_inkl / 1.25, 0)

    # Fallback: pro-rater månedstotal (ugens mandag bestemmer måned)
    mp = _mp_map_alle()
    mp_inkl = mp.get((mandag.year, mandag.month), 0.0)
    if not mp_inkl:
        return 0.0
    days = _mr(mandag.year, mandag.month)[1]
    return round((mp_inkl / 1.25) / days * 7, 0)


def gem_mobilepay_dag(linjer: list) -> int:
    """Gem/opdater daglig MobilePay-omsætning.
    linjer = [{dato, omsaetning_netto, gebyr?, omsaetning_inkl?, kilde?}]

    Hvis kun omsaetning_inkl er givet (fra gammel API), bruges det som netto.
    """
    with _conn() as conn:
        count = 0
        for l in linjer:
            dato = l["dato"]
            omsaetning_netto = l.get("omsaetning_netto") or l.get("omsaetning_inkl", 0)
            gebyr = l.get("gebyr", 0)
            omsaetning_inkl = l.get("omsaetning_inkl", omsaetning_netto + gebyr)
            kilde = l.get("kilde", "api")

            conn.execute("""
                INSERT INTO mobilepay_dag (dato, omsaetning_netto, gebyr, omsaetning_inkl, kilde)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(dato) DO UPDATE SET
                    omsaetning_netto = excluded.omsaetning_netto,
                    gebyr            = excluded.gebyr,
                    omsaetning_inkl  = excluded.omsaetning_inkl,
                    kilde            = excluded.kilde,
                    indlæst          = datetime('now','localtime')
            """, (dato, omsaetning_netto, gebyr, omsaetning_inkl, kilde))
            count += 1
    return count


def hent_mobilepay_dag(fra_dato: str = None, til_dato: str = None) -> List[Dict]:
    with _conn() as conn:
        if fra_dato and til_dato:
            rows = conn.execute(
                "SELECT dato, omsaetning_netto, gebyr, omsaetning_inkl, kilde FROM mobilepay_dag WHERE dato BETWEEN ? AND ? ORDER BY dato DESC",
                (fra_dato, til_dato)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT dato, omsaetning_netto, gebyr, omsaetning_inkl, kilde FROM mobilepay_dag ORDER BY dato DESC LIMIT 90"
            ).fetchall()
    return [dict(r) for r in rows]


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

    resultat = []
    for r in rows:
        d = _date.fromisoformat(r["min_dato"])
        iso = d.isocalendar()
        try:
            mp_netto = _mp_uge_netto(iso[0], iso[1])
        except Exception:
            mp_netto = 0.0
        row = dict(r)
        row["uge"] = iso[1]  # Brug ISO week i stedet for %W
        row["aar"] = iso[0]  # Brug ISO år
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
                   CASE WHEN COUNT(CASE WHEN bon_nr != '' THEN 1 END) > 0
                        THEN COUNT(DISTINCT CASE WHEN bon_nr != '' THEN bon_nr END)
                        ELSE COUNT(*)
                   END AS linjer
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
                ROUND(SUM(CASE WHEN CAST(CAST(varenummer AS REAL) AS INTEGER) IN (
                    SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                    FROM ugebestillinger WHERE varenummer != '' AND varenummer != '0'
                ) THEN kostpris ELSE 0 END), 2) AS kostpris,
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
                   ROUND(SUM(CASE WHEN CAST(CAST(varenummer AS REAL) AS INTEGER) IN (
                       SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                       FROM ugebestillinger WHERE varenummer != '' AND varenummer != '0'
                   ) THEN kostpris ELSE 0 END), 2) AS kostpris,
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
            "SELECT uge, aar, faktura, retur_ialt FROM bager_regnskab WHERE aar=? OR aar=?",
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

        # Sum baker-fakturaer per måned — fordeles efter salget ved månedsskift
        faktura_maaned: Dict = {}
        for br in bager_rows:
            try:
                fakt_netto = round((br["faktura"] or 0) - (br["retur_ialt"] or 0), 2)
                if fakt_netto <= 0:
                    continue
                y, w = int(br["aar"]), int(br["uge"])
                mon = _date.fromisocalendar(y, w, 1)
                son = mon + _td(days=6)

                if y != aar:
                    continue

                # Hvis ugen ligger helt i én måned: simpel sum
                if mon.month == son.month:
                    faktura_maaned[mon.month] = round(
                        faktura_maaned.get(mon.month, 0.0) + fakt_netto, 2
                    )
                else:
                    # Uge går over månedsskift: fordel efter salget på dagene
                    # Hent dagligt salg for ugen
                    mon_dato = _date.fromisocalendar(y, w, 1)
                    son_dato = mon_dato + _td(days=6)
                    dag_data = conn.execute("""
                        SELECT dato,
                               ROUND(COALESCE(SUM(omsætning)/1.25, 0), 2) AS omsat_ex_dag
                        FROM v_transaktioner
                        WHERE dato >= ? AND dato <= ?
                        GROUP BY dato ORDER BY dato
                    """, (mon_dato.isoformat(), son_dato.isoformat())).fetchall()

                    # Beregn salg per måned
                    salg_maaned = {}
                    total_salg = 0.0
                    for dag_row in dag_data:
                        dag_dato = _date.fromisoformat(dag_row["dato"])
                        omsat = dag_row["omsat_ex_dag"] or 0
                        salg_maaned[dag_dato.month] = salg_maaned.get(dag_dato.month, 0.0) + omsat
                        total_salg += omsat

                    # Fordel faktura efter salget
                    if total_salg > 0:
                        for m, salg in salg_maaned.items():
                            andel = salg / total_salg
                            faktura_maaned[m] = round(
                                faktura_maaned.get(m, 0.0) + fakt_netto * andel, 2
                            )
            except Exception:
                pass

        # MobilePay netto per måned (÷1.25) — henter fra begge kilder via _mp_map_alle
        _mp_all = _mp_map_alle()
        mp_netto_maaned: Dict = {
            m: round(v / 1.25, 0)
            for (y, m), v in _mp_all.items()
            if y == aar and v > 0
        }

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


def hent_margin_analyse(aar: int = None, kategori: str = None) -> List[Dict]:
    """Margin-analyse per produkt med detaljer."""
    with _conn() as conn:
        where_clauses = ["varenavn != ''"]
        params = []

        if aar:
            where_clauses.append("strftime('%Y', dato) = ?")
            params.append(str(aar))

        if kategori:
            where_clauses.append("kategori = ?")
            params.append(kategori)

        where_sql = " AND ".join(where_clauses)

        rows = conn.execute(f"""
            SELECT
                varenavn,
                MAX(kategori) AS kategori,
                ROUND(SUM(omsætning)/1.25, 2) AS omsat_ex_moms,
                ROUND(COALESCE(SUM(vf_korrekt), 0), 2) AS vareforbrug,
                ROUND(SUM(antal), 0) AS antal_solgt,
                ROUND(COALESCE(SUM(db_korrekt), 0), 2) AS db_kr,
                ROUND(COALESCE(SUM(db_korrekt), 0)*1.25/NULLIF(SUM(omsætning),0)*100, 1) AS db_pct,
                MAX(dato) AS seneste_salg
            FROM v_transaktioner
            WHERE {where_sql}
            GROUP BY varenavn
            ORDER BY COALESCE(db_pct, 0) DESC, omsat_ex_moms DESC
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

    # Sektion beregnes dynamisk fra varenavn — bagerens varenumre ≠ Shopbox SKU'er
    _SEK_CASE = """
        CASE
          WHEN LOWER(varenavn) LIKE '%kage%'
            OR LOWER(varenavn) LIKE '%cookie%'
            OR LOWER(varenavn) LIKE '%muffin%'
            OR LOWER(varenavn) LIKE '%brownie%'
            OR LOWER(varenavn) LIKE '%romkugl%'
            OR LOWER(varenavn) LIKE '%kokostoppe%'
            OR LOWER(varenavn) LIKE '%napoleonshat%'
            OR LOWER(varenavn) LIKE '%studenterbr%'
            OR LOWER(varenavn) LIKE '%snitter%' THEN 4
          WHEN LOWER(varenavn) LIKE '%croissant%'
            OR LOWER(varenavn) LIKE '%snegl%'
            OR LOWER(varenavn) LIKE '%snurrer%'
            OR LOWER(varenavn) LIKE '%tebirkes%'
            OR LOWER(varenavn) LIKE '%grovbirkes%'
            OR LOWER(varenavn) LIKE '%fastelavns%'
            OR LOWER(varenavn) LIKE '%wienerbr%'
            OR LOWER(varenavn) LIKE '%wienerstang%'
            OR LOWER(varenavn) LIKE '%kanelstang%'
            OR LOWER(varenavn) LIKE '%spandauer%'
            OR LOWER(varenavn) LIKE '%frøsnapper%'
            OR LOWER(varenavn) LIKE '%marcipan%'
            OR LOWER(varenavn) LIKE '%romsnegl%' THEN 3
          WHEN LOWER(varenavn) LIKE '%bolle%'
            OR LOWER(varenavn) LIKE '%musli%'
            OR LOWER(varenavn) LIKE '%hveder%' THEN 2
          ELSE 1
        END
    """

    with _conn() as conn:
        bestil = conn.execute(f"""
            SELECT varenummer, varenavn,
                   ({_SEK_CASE}) AS sektion,
                   COALESCE(man,0) AS man, COALESCE(tir,0) AS tir,
                   COALESCE(ons,0) AS ons, COALESCE(tor,0) AS tor,
                   COALESCE(fre,0) AS fre, COALESCE(loe,0) AS loe,
                   COALESCE(son,0) AS son
            FROM ugebestillinger
            WHERE uge = ? AND aar = ?
            ORDER BY ({_SEK_CASE}) ASC, rowid ASC
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
        # Fjern duplikate rækker (gamle imports uden UNIQUE constraint)
        conn.execute("""
            DELETE FROM bager_regnskab WHERE id NOT IN (
                SELECT MAX(id) FROM bager_regnskab GROUP BY uge, aar
            )
        """)
        # Opret unikt index hvis det ikke findes (migration for eksisterende databaser)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bager_uge_aar
            ON bager_regnskab(uge, aar)
        """)
        for r in linjer:
            conn.execute("""
                INSERT OR REPLACE INTO bager_regnskab
                    (uge, aar, retur_wiener, retur_boller, tgtg, b_kvali, retur_ialt, faktura)
                VALUES (?,?,?,?,?,?,?,?)
            """, (r["uge"], r["aar"], r.get("retur_wiener", 0), r.get("retur_boller", 0),
                  r.get("tgtg", 0), r.get("b_kvali", 0), r.get("retur_ialt", 0), r.get("faktura", 0)))
    return len(linjer)


def gem_tgtg_poser(poser: List[Dict]) -> int:
    """Gem/opdater pose-definitioner (navn, kreditpris, kostpris_pose, enheder_per_pose, item_id)."""
    with _conn() as conn:
        for p in poser:
            conn.execute("""
                INSERT INTO tgtg_poser (item_id, navn, kreditpris, kostpris_pose, enheder_per_pose, aktiv)
                VALUES (?,?,?,?,?,1)
                ON CONFLICT(navn) DO UPDATE SET
                    item_id=excluded.item_id,
                    kreditpris=excluded.kreditpris,
                    kostpris_pose=CASE WHEN excluded.kostpris_pose > 0
                                       THEN excluded.kostpris_pose
                                       ELSE tgtg_poser.kostpris_pose END,
                    enheder_per_pose=CASE WHEN excluded.enheder_per_pose > 0
                                          THEN excluded.enheder_per_pose
                                          ELSE tgtg_poser.enheder_per_pose END,
                    aktiv=1
            """, (p.get("item_id",""), p["navn"], p["kreditpris"],
                  p.get("kostpris_pose", 0), p.get("enheder_per_pose", 1)))
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
            "SELECT item_id, navn, kreditpris, kostpris_pose FROM tgtg_poser WHERE aktiv=1 ORDER BY navn"
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

    # Berig per_pose med kostpris_pose, vareforbrug og % tab
    kostpris_map = {r["navn"]: r["kostpris_pose"] for r in poser}
    per_pose_list = []
    for r in per_pose:
        d = dict(r)
        kp = kostpris_map.get(d["pose_navn"], 0) or 0
        vareforbrug = round(d["total_antal"] * kp, 2)
        tab_pct = round((vareforbrug - d["total_kr"]) / vareforbrug * 100, 1) if vareforbrug > 0 else None
        d["kostpris_pose"] = kp
        d["vareforbrug"]   = vareforbrug
        d["tab_pct"]       = tab_pct
        per_pose_list.append(d)

    return {
        "dage":     [dict(r) for r in dage_rows],
        "uger":     uger,
        "per_pose": per_pose_list,
        "poser":    [dict(r) for r in poser],
    }


def hent_svind_data(aar: int = None) -> List[Dict]:
    """Kombinerer bestilling, bager_regnskab og kassesalg per uge.
    Effektivt solgt = kassesalg_stk + KW-kombostk + TGTG_stk.
    TGTG stk: faktiske enheder fra tgtg_dagssalg (dato = produktionsdato = salgsdag-1).
    Fallback: tgtg_kr ÷ 38 kr/pose hvis ingen tgtg_dagssalg data.
    """
    TGTG_KR_PR_POSE = 38.0
    TGTG_ENHEDER_PR_POSE = 5.5  # gns. enheder pr. pose (Lykke=6, Brød=5, Wiener=6)

    from datetime import date as _date

    with _conn() as conn:
        # Shopbox total omsætning per dag (inkl. moms) → summeres til ISO-uge
        shopbox_dage = conn.execute("""
            SELECT dato, ROUND(SUM(omsætning), 2) AS dagomsat
            FROM v_transaktioner
            GROUP BY dato
        """).fetchall()
        shopbox_uge_map: Dict = {}
        for r in shopbox_dage:
            iso = _date.fromisoformat(r["dato"]).isocalendar()
            key = (iso[1], iso[0])   # (uge, aar)
            shopbox_uge_map[key] = shopbox_uge_map.get(key, 0.0) + (r["dagomsat"] or 0.0)

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

        # TGTG faktisk stk fra tgtg_dagssalg — antal × enheder_per_pose (ikke bare poser)
        tgtg_dage = conn.execute("""
            SELECT ds.dato,
                   SUM(ds.antal * COALESCE(tp.enheder_per_pose, 1)) AS stk
            FROM tgtg_dagssalg ds
            LEFT JOIN tgtg_poser tp ON ds.item_id = tp.item_id
            GROUP BY ds.dato
        """).fetchall()
        tgtg_stk_map: Dict = {}
        for r in tgtg_dage:
            iso = _date.fromisoformat(r["dato"]).isocalendar()
            key = (iso[1], iso[0])
            tgtg_stk_map[key] = tgtg_stk_map.get(key, 0) + int(r["stk"] or 0)

        aar_filter1 = "AND b.aar = ?" if aar else ""
        aar_params  = (aar,) if aar else ()
        rows = conn.execute(f"""
            SELECT
                b.uge, b.aar,
                ROUND(SUM(u.total_antal), 0)                   AS bestilt_stk,
                ROUND(SUM(u.total_pris),  2)                   AS bestilt_kr,
                b.retur_wiener, b.retur_boller, b.tgtg, b.b_kvali, b.retur_ialt,
                b.faktura,
                ROUND(b.faktura - b.retur_ialt, 2)             AS netto_kr
            FROM bager_regnskab b
            LEFT JOIN ugebestillinger u ON u.uge = b.uge AND u.aar = b.aar
            WHERE 1=1 {aar_filter1}
            GROUP BY b.uge, b.aar
            ORDER BY b.aar DESC, b.uge DESC
            LIMIT 12
        """, aar_params).fetchall()

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
            tgtg_stk       = round(d["tgtg"] / TGTG_KR_PR_POSE * TGTG_ENHEDER_PR_POSE) if d.get("tgtg") else 0
            tgtg_stk_kilde = "estimat"

        # MobilePay netto: brug faktiske daglige data hvis tilgængelige, ellers pro-rata
        try:
            mp_netto = _mp_uge_netto(int(d["aar"]), int(d["uge"]))
        except Exception:
            mp_netto = 0.0

        # Shopbox omsætning for ugen (inkl. moms) + netto ex-moms
        shopbox_inkl  = round(shopbox_uge_map.get((d["uge"], d["aar"]), 0.0) or 0.0, 0)
        shopbox_netto = round(shopbox_inkl / 1.25, 0)

        d["kassesalg_stk"]    = kassesalg
        d["kw_stk"]           = kw_stk
        d["tgtg_stk"]         = tgtg_stk
        d["tgtg_stk_kilde"]   = tgtg_stk_kilde
        d["shopbox_inkl"]     = shopbox_inkl
        d["shopbox_netto"]    = shopbox_netto
        d["mp_netto"]         = mp_netto
        # Total netto omsætning = Shopbox netto + MobilePay netto
        d["total_omsat_netto"] = round(shopbox_netto + mp_netto, 0)
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


def hent_dag_db_detalje() -> Dict:
    """DB-detaljer per produkt for seneste dato med data — bruges til fejlfinding."""
    with _conn() as conn:
        seneste_dato = conn.execute("SELECT MAX(dato) FROM transaktioner").fetchone()[0]
        if not seneste_dato:
            return {"dato": None, "produkter": [], "total": {}}

        rows = conn.execute("""
            SELECT
                t.varenavn,
                t.kategori,
                ROUND(SUM(t.antal), 0)      AS antal,
                ROUND(SUM(t.omsætning), 2)  AS omsat_inkl,
                ROUND(SUM(t.vf_korrekt), 2) AS vf,
                ROUND(SUM(t.db_korrekt), 2) AS db_kr,
                ROUND(SUM(t.db_korrekt)*1.25 / NULLIF(SUM(t.omsætning),0) * 100, 1) AS db_pct,
                MAX(CASE WHEN s.pris_ex_moms > 0 THEN 1 ELSE 0 END) AS har_stamdata
            FROM v_transaktioner t
            LEFT JOIN varestamdata s ON t.varenummer = s.sku AND t.varenummer != ''
            WHERE t.dato = ?
            GROUP BY t.varenavn, t.kategori
            ORDER BY SUM(t.omsætning) DESC
        """, (seneste_dato,)).fetchall()

        total = conn.execute("""
            SELECT
                ROUND(SUM(omsætning), 2)   AS omsat_inkl,
                ROUND(SUM(vf_korrekt), 2)  AS vf,
                ROUND(SUM(db_korrekt), 2)  AS db_kr,
                ROUND(SUM(db_korrekt)*1.25 / NULLIF(SUM(omsætning),0) * 100, 1) AS db_pct
            FROM v_transaktioner WHERE dato = ?
        """, (seneste_dato,)).fetchone()

    return {
        "dato": seneste_dato,
        "produkter": [dict(r) for r in rows],
        "total": dict(total) if total else {}
    }


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


# ── SPILD-RAPPORT ─────────────────────────────────────────────────────────────

def hent_spild_uge_overblik(uge: int, aar: int) -> Dict:
    """Spild-overblik for én uge — bruger samme beregning som spild-rapporten.
    Ekskluderer dags dato (uafsluttet dag) fra beregningen.
    """
    from datetime import date as _d, timedelta as _td
    idag = _d.today().isoformat()
    try:
        d = hent_spild_dagsniveau(uge, aar)
        if "error" in d:
            return {"uge": uge, "aar": aar, "har_data": False}

        # Filtrer kun afsluttede dage (ekskl. i dag)
        dage = [dag for dag in d.get("dage", []) if dag["har_data"] and dag["dato"] < idag]
        if not dage:
            return {"uge": uge, "aar": aar, "har_data": False}

        bestilt   = sum(dag["bestilt"]   for dag in dage)
        kassesalg = sum(dag["kassesalg"] for dag in dage)
        tgtg      = sum(dag["tgtg"]      for dag in dage)
        svind_stk = sum(dag["svind"] or 0 for dag in dage)
        svind_pct = round(svind_stk / bestilt * 100, 1) if bestilt > 0 else None
        n_dage    = len(dage)

        return {
            "uge": uge, "aar": aar, "har_data": True,
            "bestilt": bestilt, "kassesalg": kassesalg, "tgtg": tgtg,
            "svind": svind_stk, "svind_pct": svind_pct,
            "n_dage": n_dage, "er_komplet": n_dage >= 6,
        }
    except Exception as e:
        return {"uge": uge, "aar": aar, "har_data": False, "fejl": str(e)}


def hent_spild_dagsniveau(uge: int, aar: int) -> Dict:
    """Dag-niveau spild-data for en ISO-uge.

    TGTG-offset: TGTG solgt på dato D stammer fra produktion D-1.
    Så for produktionsdag D hentes tgtg_dagssalg WHERE dato = D+1.
    """
    from datetime import date as _date, timedelta

    # Mandag i ISO-ugen
    jan4 = _date(aar, 1, 4)
    man = jan4 - timedelta(days=jan4.weekday()) + timedelta(weeks=uge - 1)

    dag_navne   = ['man', 'tir', 'ons', 'tor', 'fre', 'loe', 'son']
    dag_labels  = ['Mandag', 'Tirsdag', 'Onsdag', 'Torsdag', 'Fredag', 'Lørdag', 'Søndag']

    datoer = [man + timedelta(days=i) for i in range(7)]
    dato_start = datoer[0].isoformat()
    dato_slut  = datoer[6].isoformat()

    # Navigation
    prev_uge = uge - 1
    prev_aar = aar
    if prev_uge < 1:
        prev_uge = 52
        prev_aar = aar - 1
    next_uge = uge + 1
    next_aar = aar
    if next_uge > 52:
        next_uge = 1
        next_aar = aar + 1

    _KAFFE_LIKE = (
        "LOWER(varenavn) LIKE '%kaffe%' OR LOWER(varenavn) LIKE '%flat white%' "
        "OR LOWER(varenavn) LIKE '%cappuccino%' OR LOWER(varenavn) LIKE '%americano%' "
        "OR LOWER(varenavn) LIKE '%latte%' OR LOWER(varenavn) LIKE '%espresso%' "
        "OR LOWER(varenavn) LIKE '%macchiato%' OR LOWER(varenavn) LIKE '%cortado%' "
        "OR LOWER(varenavn) LIKE '%lungo%' OR LOWER(varenavn) LIKE '%mocha%'"
    )
    _WIENER_LIKE = (
        "LOWER(varenavn) LIKE '%wiener%' OR LOWER(varenavn) LIKE '%kanelsnegl%' "
        "OR LOWER(varenavn) LIKE '%spandauer%' OR LOWER(varenavn) LIKE '%croissant%'"
    )
    # KBMO = Kaffe + Bolle med Ost: 1 bolle ryger per kombosalg
    _BOLLE_OST_LIKE = (
        "LOWER(varenavn) LIKE '%bolle%ost%' OR LOWER(varenavn) LIKE '%ost%bolle%' "
        "OR LOWER(varenavn) LIKE '%bolle m.%ost%' OR LOWER(varenavn) LIKE '%bmo%'"
    )
    # Returprocenter — hvad der KAN sendes retur til bageren hvis ikke solgt
    RETUR_BOLLER_PCT  = 0.10
    RETUR_WIENER_PCT  = 0.135
    # Varenavn-match i bestillinger — ALLE bolle- og wienerbrøds-varianter
    _BOLLE_BESTIL = (
        "LOWER(varenavn) LIKE '%bolle%'"
        " OR LOWER(varenavn) LIKE '%hveder%'"
        " OR LOWER(varenavn) LIKE '%musli%'"
        " OR LOWER(varenavn) LIKE '%teboller%'"
    )
    _WIENER_BESTIL = (
        "LOWER(varenavn) LIKE '%wiener%'"
        " OR LOWER(varenavn) LIKE '%croissant%'"
        " OR LOWER(varenavn) LIKE '%snegl%'"
        " OR LOWER(varenavn) LIKE '%snurrer%'"
        " OR LOWER(varenavn) LIKE '%tebirkes%'"
        " OR LOWER(varenavn) LIKE '%grovbirkes%'"
        " OR LOWER(varenavn) LIKE '%fastelavns%'"
        " OR LOWER(varenavn) LIKE '%spandauer%'"
        " OR LOWER(varenavn) LIKE '%frøsnapper%'"
        " OR LOWER(varenavn) LIKE '%kanelstang%'"
        " OR LOWER(varenavn) LIKE '%wienerstang%'"
        " OR LOWER(varenavn) LIKE '%marcipan%'"
    )
    # Kager identificeres på varenavn — bagerens varenumre i ugebestillinger
    # er IKKE Shopbox SKU'er, så varenavn-mønstre er den eneste pålidelige metode.
    _KAGE_VN = (
        "LOWER(varenavn) LIKE '%kage%'"
        " OR LOWER(varenavn) LIKE '%cookie%'"
        " OR LOWER(varenavn) LIKE '%muffin%'"
        " OR LOWER(varenavn) LIKE '%brownie%'"
        " OR LOWER(varenavn) LIKE '%romkugl%'"
        " OR LOWER(varenavn) LIKE '%kokostoppe%'"
        " OR LOWER(varenavn) LIKE '%napoleonshat%'"
        " OR LOWER(varenavn) LIKE '%studenterbr%'"
        " OR LOWER(varenavn) LIKE '%snitter%'"
        " OR LOWER(varenavn) LIKE '%stammer%'"
        " OR LOWER(varenavn) LIKE '%honningbomb%'"
        " OR LOWER(varenavn) LIKE '%honninghjerter%'"
    )
    _KAGE_FILTER = f"({_KAGE_VN})"

    try:
        with _conn() as conn:
            # ── Bestillinger per dag for denne uge ──────────────────────────
            # ugebestillinger kolonner: man, tir, ons, tor, fre, loe, son
            bestil_rows = conn.execute(f"""
                SELECT
                    SUM(man) AS man, SUM(tir) AS tir, SUM(ons) AS ons,
                    SUM(tor) AS tor, SUM(fre) AS fre, SUM(loe) AS loe,
                    SUM(son) AS son
                FROM ugebestillinger
                WHERE uge = ? AND aar = ?
                  AND NOT {_KAGE_FILTER}
            """, (uge, aar)).fetchone()

            bestil_per_dag = {}
            if bestil_rows:
                for dag in dag_navne:
                    bestil_per_dag[dag] = int(bestil_rows[dag] or 0)

            # ── Retur-mulig per dag: boller 10%, wienerbrød 13,5% ─────────────
            bestil_boller_row = conn.execute(f"""
                SELECT
                    SUM(man) AS man, SUM(tir) AS tir, SUM(ons) AS ons,
                    SUM(tor) AS tor, SUM(fre) AS fre, SUM(loe) AS loe,
                    SUM(son) AS son
                FROM ugebestillinger
                WHERE uge = ? AND aar = ?
                  AND ({_BOLLE_BESTIL})
                  AND NOT {_KAGE_FILTER}
            """, (uge, aar)).fetchone()
            bestil_wiener_row = conn.execute(f"""
                SELECT
                    SUM(man) AS man, SUM(tir) AS tir, SUM(ons) AS ons,
                    SUM(tor) AS tor, SUM(fre) AS fre, SUM(loe) AS loe,
                    SUM(son) AS son
                FROM ugebestillinger
                WHERE uge = ? AND aar = ?
                  AND ({_WIENER_BESTIL})
                  AND NOT {_KAGE_FILTER}
            """, (uge, aar)).fetchone()
            retur_per_dag = {}
            for dag in dag_navne:
                b_b = float(bestil_boller_row[dag] or 0) if bestil_boller_row else 0.0
                b_w = float(bestil_wiener_row[dag] or 0) if bestil_wiener_row else 0.0
                retur_per_dag[dag] = math.ceil(b_b * RETUR_BOLLER_PCT + b_w * RETUR_WIENER_PCT)

            # ── Kassesalg per dato ────────────────────────────────────────────
            # Matcher varenumre fra bestillinger (ekskl. kaffe/drikkevarer)
            dato_liste = [d.isoformat() for d in datoer]
            placeholders = ','.join('?' * 7)
            kasse_rows = conn.execute(f"""
                SELECT dato, ROUND(SUM(antal), 0) AS antal
                FROM transaktioner
                WHERE dato IN ({placeholders})
                  AND CAST(CAST(varenummer AS REAL) AS INTEGER) IN (
                      SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                      FROM ugebestillinger
                      WHERE varenummer != '' AND varenummer != '0'
                        AND NOT {_KAGE_FILTER}
                  )
                GROUP BY dato
            """, dato_liste).fetchall()
            kasse_map = {r['dato']: int(r['antal'] or 0) for r in kasse_rows}

            # ── TGTG per produktionsdag (D+1 offset) ─────────────────────────
            # Produktionsdag D → TGTG afhentes af kunder dagen efter (D+1)
            # Eksempel: mandag rester listes aften → tirsdag morgen afhentes → tgtg_dagssalg.dato = tirsdag
            # Derfor: for at finde mandags TGTG, slår vi op på tirsdagens salg
            tgtg_salgs_datoer = [(d + timedelta(days=1)).isoformat() for d in datoer]
            ph_tgtg = ','.join('?' * len(tgtg_salgs_datoer))
            tgtg_rows = conn.execute(f"""
                SELECT ds.dato,
                       SUM(ds.antal) AS poser,
                       SUM(ds.antal * COALESCE(tp.enheder_per_pose, 1)) AS stk
                FROM tgtg_dagssalg ds
                LEFT JOIN tgtg_poser tp ON ds.item_id = tp.item_id
                WHERE ds.dato IN ({ph_tgtg})
                GROUP BY ds.dato
            """, tgtg_salgs_datoer).fetchall()
            # Map: produktionsdato → {poser, stk}  (salgsdag - 1 dag = produktionsdag)
            tgtg_map  = {}
            tgtg_poser_map = {}
            for r in tgtg_rows:
                prod_dato = (_date.fromisoformat(r['dato']) - timedelta(days=1)).isoformat()
                tgtg_map[prod_dato]       = int(r['stk']   or 0)
                tgtg_poser_map[prod_dato] = int(r['poser'] or 0)

            # ── KW kombos per dato ────────────────────────────────────────────
            # COUNT(DISTINCT bon_nr) på boner der har BÅDE kaffe og wiener
            kw_rows = conn.execute(f"""
                SELECT dato, COUNT(DISTINCT bon_nr) AS kw_antal
                FROM transaktioner
                WHERE dato IN ({placeholders})
                  AND bon_nr != ''
                  AND bon_nr IN (
                      SELECT bon_nr FROM transaktioner
                      WHERE dato IN ({placeholders}) AND ({_KAFFE_LIKE})
                  )
                  AND bon_nr IN (
                      SELECT bon_nr FROM transaktioner
                      WHERE dato IN ({placeholders}) AND ({_WIENER_LIKE})
                  )
                GROUP BY dato
            """, dato_liste + dato_liste + dato_liste).fetchall()
            kw_map = {r['dato']: int(r['kw_antal'] or 0) for r in kw_rows}

            # ── KBMO kombos per dato (Kaffe + Bolle med Ost = 1 bolle) ──────────
            kbmo_rows = conn.execute(f"""
                SELECT dato, COUNT(DISTINCT bon_nr) AS kbmo_antal
                FROM transaktioner
                WHERE dato IN ({placeholders})
                  AND bon_nr != ''
                  AND bon_nr IN (
                      SELECT bon_nr FROM transaktioner
                      WHERE dato IN ({placeholders}) AND ({_KAFFE_LIKE})
                  )
                  AND bon_nr IN (
                      SELECT bon_nr FROM transaktioner
                      WHERE dato IN ({placeholders}) AND ({_BOLLE_OST_LIKE})
                  )
                GROUP BY dato
            """, dato_liste + dato_liste + dato_liste).fetchall()
            kbmo_map = {r['dato']: int(r['kbmo_antal'] or 0) for r in kbmo_rows}

            # ── Kategori-fordeling kassesalg denne uge (ekskl. kager) ───────────
            kat_rows = conn.execute(f"""
                SELECT
                    COALESCE(NULLIF(kategori,''), 'Ukendt') AS kat,
                    ROUND(SUM(antal), 0) AS kassesalg,
                    ROUND(SUM(omsætning), 2) AS omsaetning
                FROM transaktioner
                WHERE dato IN ({placeholders})
                  AND CAST(CAST(varenummer AS REAL) AS INTEGER) IN (
                      SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                      FROM ugebestillinger
                      WHERE varenummer != '' AND varenummer != '0'
                        AND NOT {_KAGE_FILTER}
                  )
                GROUP BY kat
                ORDER BY kassesalg DESC
            """, dato_liste).fetchall()
            kategorier = [dict(r) for r in kat_rows]

            # ── Kage-sektion: ugentlige tal — SKU-baseret matching ───────────
            kage_bestil_rows = conn.execute(f"""
                SELECT varenavn, varenummer,
                       (man+tir+ons+tor+fre+loe+son) AS uge_total
                FROM ugebestillinger
                WHERE uge = ? AND aar = ?
                  AND {_KAGE_FILTER}
                  AND varenummer != '' AND varenummer != '0'
                ORDER BY uge_total DESC
            """, (uge, aar)).fetchall()
            # Fallback: kager uden varenummer matches på varenavn
            kage_bestil_ingen_sku = conn.execute(f"""
                SELECT varenavn,
                       (man+tir+ons+tor+fre+loe+son) AS uge_total
                FROM ugebestillinger
                WHERE uge = ? AND aar = ?
                  AND {_KAGE_FILTER}
                  AND (varenummer = '' OR varenummer = '0' OR varenummer IS NULL)
                ORDER BY uge_total DESC
            """, (uge, aar)).fetchall()

            # SKU → {varenavn, bestilt}
            kage_sku_map = {
                str(int(float(r['varenummer']))): {'varenavn': r['varenavn'],
                                                    'bestilt': int(r['uge_total'] or 0)}
                for r in kage_bestil_rows if r['varenummer']
            }
            kage_bestilt_total = sum(v['bestilt'] for v in kage_sku_map.values())
            kage_bestilt_total += sum(int(r['uge_total'] or 0) for r in kage_bestil_ingen_sku)

            # Kassesalg per SKU fra transaktioner
            kage_kasse_rows = conn.execute(f"""
                SELECT CAST(CAST(varenummer AS REAL) AS INTEGER) AS sku,
                       COALESCE(varenavn,'') AS varenavn,
                       ROUND(SUM(antal), 0) AS antal
                FROM transaktioner
                WHERE dato IN ({placeholders})
                  AND CAST(CAST(varenummer AS REAL) AS INTEGER) IN (
                      SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                      FROM ugebestillinger
                      WHERE varenummer != '' AND varenummer != '0'
                        AND {_KAGE_FILTER}
                  )
                GROUP BY sku
            """, dato_liste).fetchall()
            kage_sku_solgt = {str(r['sku']): int(r['antal'] or 0) for r in kage_kasse_rows}
            kage_kassesalg_total = sum(kage_sku_solgt.values())

            # Byg kage-vareliste (SKU-matchede)
            kage_varer = []
            for sku, info in sorted(kage_sku_map.items(), key=lambda x: -x[1]['bestilt']):
                bestilt_k = info['bestilt']
                solgt_k   = kage_sku_solgt.get(sku, 0)
                svind_k   = max(0, bestilt_k - solgt_k)
                pct_k     = round(svind_k / bestilt_k * 100, 1) if bestilt_k > 0 else None
                kage_varer.append({
                    'varenavn': info['varenavn'],
                    'bestilt':  bestilt_k,
                    'solgt':    solgt_k,
                    'svind':    svind_k,
                    'svind_pct': pct_k,
                })
            # Tilføj kager uden SKU (varenavn-fallback)
            kage_norm_solgt = {r['varenavn'].strip().lower(): int(r['antal'] or 0)
                               for r in conn.execute(f"""
                SELECT varenavn, ROUND(SUM(antal),0) AS antal
                FROM transaktioner WHERE dato IN ({placeholders}) AND ({_KAGE_VN})
                GROUP BY varenavn
            """, dato_liste).fetchall()}
            for r in kage_bestil_ingen_sku:
                bestilt_k = int(r['uge_total'] or 0)
                solgt_k   = kage_norm_solgt.get(r['varenavn'].strip().lower(), 0)
                svind_k   = max(0, bestilt_k - solgt_k)
                pct_k     = round(svind_k / bestilt_k * 100, 1) if bestilt_k > 0 else None
                kage_varer.append({
                    'varenavn': r['varenavn'],
                    'bestilt':  bestilt_k,
                    'solgt':    solgt_k,
                    'svind':    svind_k,
                    'svind_pct': pct_k,
                })
            kage_svind_total = max(0, kage_bestilt_total - kage_kassesalg_total)
            kage_svind_pct   = round(kage_svind_total / kage_bestilt_total * 100, 1) \
                               if kage_bestilt_total > 0 else None
            kage_sektion = {
                'bestilt':   kage_bestilt_total,
                'kassesalg': kage_kassesalg_total,
                'svind':     kage_svind_total,
                'svind_pct': kage_svind_pct,
                'varer':     kage_varer,
            }

            # ── Registrerede returneringer per dag ────────────────────────────
            retur_reg_dag_rows = conn.execute("""
                SELECT registreret_dato,
                       SUM(CASE WHEN kategori='boller' THEN antal ELSE 0 END) AS boller,
                       SUM(CASE WHEN kategori='wienerbroed' THEN antal ELSE 0 END) AS wiener,
                       SUM(antal) AS total
                FROM retur_detaljer
                WHERE uge = ? AND aar = ?
                GROUP BY registreret_dato
            """, (uge, aar)).fetchall()
            # Map: dato → {boller, wiener, total}
            retur_dag_map = {
                r['registreret_dato']: {
                    'boller': int(r['boller'] or 0),
                    'wiener': int(r['wiener'] or 0),
                    'total':  int(r['total']  or 0),
                }
                for r in retur_reg_dag_rows
            }
            retur_registreret_total = sum(v['total'] for v in retur_dag_map.values())
            retur_boller_reg = sum(v['boller'] for v in retur_dag_map.values())
            retur_wiener_reg = sum(v['wiener'] for v in retur_dag_map.values())
            retur_registreret = [dict(r) for r in conn.execute("""
                SELECT produkt, SUM(antal) AS antal, kategori
                FROM retur_detaljer WHERE uge=? AND aar=?
                GROUP BY produkt, kategori ORDER BY antal DESC
            """, (uge, aar)).fetchall()]

            # ── Historiske snit: seneste 4 uger per ugedag ────────────────────
            # Beregn de 4 foregående uger (ekskl. indeværende)
            hist_bestil: Dict[str, list] = {d: [] for d in dag_navne}
            hist_svind_pct: Dict[str, list] = {d: [] for d in dag_navne}

            for w in range(1, 5):
                h_uge = uge - w
                h_aar = aar
                while h_uge < 1:
                    h_uge += 52
                    h_aar -= 1
                h_jan4 = _date(h_aar, 1, 4)
                h_man = h_jan4 - timedelta(days=h_jan4.weekday()) + timedelta(weeks=h_uge - 1)
                h_datoer = [(h_man + timedelta(days=i)).isoformat() for i in range(7)]

                h_bestil = conn.execute(f"""
                    SELECT
                        SUM(man) AS man, SUM(tir) AS tir, SUM(ons) AS ons,
                        SUM(tor) AS tor, SUM(fre) AS fre, SUM(loe) AS loe,
                        SUM(son) AS son
                    FROM ugebestillinger
                    WHERE uge = ? AND aar = ?
                      AND NOT {_KAGE_FILTER}
                """, (h_uge, h_aar)).fetchone()
                if not h_bestil:
                    continue

                h_kasse_rows = conn.execute(f"""
                    SELECT dato, ROUND(SUM(antal), 0) AS antal
                    FROM transaktioner
                    WHERE dato IN ({placeholders})
                      AND CAST(CAST(varenummer AS REAL) AS INTEGER) IN (
                          SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                          FROM ugebestillinger
                          WHERE varenummer != '' AND varenummer != '0'
                      )
                    GROUP BY dato
                """, h_datoer).fetchall()
                h_kasse_map = {r['dato']: int(r['antal'] or 0) for r in h_kasse_rows}

                h_tgtg_datoer = [(_date.fromisoformat(d) + timedelta(days=1)).isoformat() for d in h_datoer]
                h_tgtg_rows = conn.execute(f"""
                    SELECT dato, SUM(antal) AS antal
                    FROM tgtg_dagssalg
                    WHERE dato IN ({placeholders})
                    GROUP BY dato
                """, h_tgtg_datoer).fetchall()
                h_tgtg_map = {}
                for r in h_tgtg_rows:
                    prod_d = (_date.fromisoformat(r['dato']) - timedelta(days=1)).isoformat()
                    h_tgtg_map[prod_d] = int(r['antal'] or 0)

                for i, dag in enumerate(dag_navne):
                    b = int(h_bestil[dag] or 0)
                    if b <= 0:
                        continue
                    dato_str = h_datoer[i]
                    k = h_kasse_map.get(dato_str, 0)
                    t = h_tgtg_map.get(dato_str, 0)
                    eff = k + t
                    svind = max(0, b - eff)
                    sp = round(svind / b * 100, 1)
                    hist_bestil[dag].append(b)
                    hist_svind_pct[dag].append(sp)

    except Exception as e:
        return {"error": str(e), "uge": uge, "aar": aar}

    # ── Byg dage ─────────────────────────────────────────────────────────────
    dage = []
    total_bestilt    = 0
    total_kassesalg  = 0
    total_rester     = 0
    total_tgtg       = 0
    total_tgtg_poser = 0
    total_kw         = 0
    total_kbmo       = 0
    total_effektivt  = 0
    total_retur      = 0
    total_svind      = 0

    for i, dag in enumerate(dag_navne):
        dato_str = datoer[i].isoformat()
        bestilt  = bestil_per_dag.get(dag, 0)
        kassesalg = kasse_map.get(dato_str, 0)
        tgtg      = tgtg_map.get(dato_str, 0)
        kw          = kw_map.get(dato_str, 0)
        kbmo        = kbmo_map.get(dato_str, 0)
        retur_mulig = retur_per_dag.get(dag, 0)
        tgtg_poser_dag = tgtg_poser_map.get(dato_str, 0)
        retur_dag  = retur_dag_map.get(dato_str, {})

        # Rester = hvad der er tilovers ved lukketid
        rester = max(0, bestilt - kassesalg - kbmo) if bestilt > 0 else 0

        effektivt   = kassesalg + tgtg + kbmo
        # Brug faktisk registreret retur hvis tilgængeligt, ellers estimat
        retur_faktisk = retur_dag.get('total', 0)
        if retur_faktisk > 0:
            retur_anvendt = retur_faktisk
        else:
            retur_mulig = min(retur_mulig, max(0, bestilt - effektivt)) if bestilt > 0 else 0
            retur_anvendt = retur_mulig
        svind       = max(0, bestilt - effektivt - retur_anvendt) if bestilt > 0 else None
        svind_pct   = round(svind / bestilt * 100, 1) if (svind is not None and bestilt > 0) else None
        rester_pct  = round(rester / bestilt * 100, 1) if bestilt > 0 else None

        avg_bestilt = round(sum(hist_bestil[dag]) / len(hist_bestil[dag]), 1) if hist_bestil[dag] else None
        avg_svind_pct = round(sum(hist_svind_pct[dag]) / len(hist_svind_pct[dag]), 1) if hist_svind_pct[dag] else None

        har_data = bestilt > 0 or kassesalg > 0

        if har_data:
            total_bestilt      += bestilt
            total_kassesalg    += kassesalg
            total_rester       += rester
            total_tgtg         += tgtg
            total_tgtg_poser   += tgtg_poser_dag
            total_kw           += kw
            total_kbmo         += kbmo
            total_effektivt    += effektivt
            total_retur        += retur_mulig
            if svind is not None:
                total_svind += svind

        dage.append({
            'dag':              dag,
            'dag_label':        dag_labels[i],
            'dato':             dato_str,
            'bestilt':          bestilt,
            'kassesalg':        kassesalg,
            'kw':               kw,
            'kbmo':             kbmo,
            'rester':           rester,
            'rester_pct':       rester_pct,
            'tgtg':             tgtg,
            'tgtg_poser':       tgtg_poser_dag,
            'retur_mulig':      retur_mulig,
            'retur_reg_boller': retur_dag.get('boller', 0),
            'retur_reg_wiener': retur_dag.get('wiener', 0),
            'retur_reg_total':  retur_dag.get('total', 0),
            'effektivt':        effektivt,
            'svind':            svind,
            'svind_pct':        svind_pct,
            'har_data':         har_data,
            'avg_bestilt_4u':   avg_bestilt,
            'avg_svind_pct_4u': avg_svind_pct,
        })

    # Brug faktisk registreret retur i total-spild hvis tilgængeligt
    if retur_registreret_total > 0:
        total_svind = max(0, total_bestilt - total_kassesalg - total_tgtg - total_kbmo - retur_registreret_total)

    total_svind_pct  = round(total_svind  / total_bestilt * 100, 1) if total_bestilt > 0 else None
    total_rester_pct = round(total_rester / total_bestilt * 100, 1) if total_bestilt > 0 else None

    # ── Anbefalinger ─────────────────────────────────────────────────────────
    anbefalinger = []
    for dag_d in dage:
        if not dag_d['har_data'] or dag_d['bestilt'] <= 0:
            continue
        dag_lbl = dag_d['dag_label']
        sp      = dag_d['svind_pct']
        svind_n = dag_d['svind'] or 0
        bestilt = dag_d['bestilt']
        avg_sp  = dag_d['avg_svind_pct_4u']
        avg_b   = dag_d['avg_bestilt_4u']

        if sp is not None and sp > 35:
            anbefalinger.append({
                'type': 'advarsel',
                'dag': dag_lbl,
                'prioritet': 1,
                'besked': f'Høj spild — reducer bestilling {dag_lbl} med ~{round(svind_n * 0.6)} stk',
            })
        elif sp is not None and sp > 20:
            anbefalinger.append({
                'type': 'advarsel',
                'dag': dag_lbl,
                'prioritet': 2,
                'besked': f'Forhøjet spild — overvej at reducere med ~{round(svind_n * 0.4)} stk',
            })

        if sp is not None and sp < 5 and bestilt > 8:
            anbefalinger.append({
                'type': 'mulighed',
                'dag': dag_lbl,
                'prioritet': 2,
                'besked': f'Lav spild — tjek om du sælger ud for tidligt',
            })

        if avg_b is not None and avg_b > 0 and bestilt > avg_b * 1.25:
            pct = round(bestilt / avg_b * 100 - 100)
            anbefalinger.append({
                'type': 'info',
                'dag': dag_lbl,
                'prioritet': 3,
                'besked': f'Bestilling {pct}% over historisk snit',
            })

        if sp is not None and avg_sp is not None and sp > avg_sp + 15:
            anbefalinger.append({
                'type': 'advarsel',
                'dag': dag_lbl,
                'prioritet': 2,
                'besked': f'Spild højere end normalt for denne dag ({sp}% vs snit {avg_sp}%)',
            })

    anbefalinger.sort(key=lambda x: -x['prioritet'])

    return {
        'uge':              uge,
        'aar':              aar,
        'dato_start':       dato_start,
        'dato_slut':        dato_slut,
        'dage':             dage,
        'kategorier':       kategorier,
        'kager':            kage_sektion,
        'total_bestilt':    total_bestilt,
        'total_kassesalg':  total_kassesalg,
        'total_rester':     total_rester,
        'total_rester_pct': total_rester_pct,
        'total_tgtg':       total_tgtg,
        'total_tgtg_poser': total_tgtg_poser,
        'total_kw':         total_kw,
        'total_kbmo':       total_kbmo,
        'total_effektivt':  total_effektivt,
        'total_retur':              total_retur,
        'retur_registreret':        retur_registreret,
        'retur_registreret_total':  retur_registreret_total,
        'retur_boller_reg':         retur_boller_reg,
        'retur_wiener_reg':         retur_wiener_reg,
        'total_svind':              total_svind,
        'total_svind_pct':          total_svind_pct,
        'anbefalinger':             anbefalinger,
        'prev_uge':         prev_uge,
        'prev_aar':         prev_aar,
        'next_uge':         next_uge,
        'next_aar':         next_aar,
    }


def hent_retur_tgtg_anbefaling(dato: str = None) -> Dict:
    """Deterministisk anbefaling: hvad skal sendes RETUR til bageren, og hvad kan
    blive til TGTG-poser — baseret på dagens bestilling og salg INDTIL NU.

    Logik pr. vare (bagværk, ekskl. kager):
      rester  = max(0, bestilt_i_dag − solgt_i_dag)
      retur   = min(rester, retur-cap)   — bageren tager kun en del retur:
                boller 10 %, wienerbrød 13,5 % af bestilt (etableret regel)
      tgtg    = rester − retur           — resten kan pakkes i TGTG-poser

    Ingen AI/gæt — kun aritmetik på faktiske tal. Mest relevant sidst på dagen.
    """
    import math as _math
    from datetime import date as _d

    RETUR_BOLLER_PCT = 0.10
    RETUR_WIENER_PCT = 0.135
    _BOLLE_KW = ('bolle', 'hveder', 'musli', 'teboller')
    _WIENER_KW = ('wiener', 'croissant', 'crossaint', 'snegl', 'snurrer', 'tebirkes',
                  'grovbirkes', 'fastelavns', 'spandauer', 'frøsnapper', 'kanelstang',
                  'wienerstang', 'marcipan', 'birkes')
    _KAGE_KW = ('kage', 'cookie', 'muffin', 'brownie', 'romkugl', 'kokostoppe',
                'napoleonshat', 'studenterbr', 'snitter', 'stammer', 'honningbomb',
                'honninghjerter')

    _BROED_KW = ('brød', 'flute', 'rugbrød', 'franskbrød', 'grovbrød', 'surdej', 'spelt')

    def _kat(navn: str) -> str:
        n = (navn or '').lower()
        if any(k in n for k in _KAGE_KW):   return 'kage'
        if any(k in n for k in _WIENER_KW): return 'wiener'   # før brød (wienerBRØD)
        if any(k in n for k in _BOLLE_KW):  return 'boller'
        if any(k in n for k in _BROED_KW):  return 'brød'
        return 'andet'

    # TGTG-pose-opskrifter (kategori → antal pr. pose). Kager udeladt bevidst.
    # Matcher jeres faktiske poser; kreditpris hentes fra tgtg_poser-tabellen.
    POSE_OPSKRIFT = [
        ('wienerbrødspose', '🥐', {'wiener': 6}),
        ('lykkepose',       '🌟', {'brød': 1, 'boller': 3, 'wiener': 2}),
        ('brødpose',        '🍞', {'brød': 2, 'boller': 3}),
    ]
    _KAT_LABEL = {'brød': 'Brød', 'boller': 'Boller', 'wiener': 'Wienerbrød'}

    with _conn() as conn:
        if not dato:
            dato = conn.execute("SELECT MAX(dato) FROM transaktioner").fetchone()[0]
        if not dato:
            return {"dato": None, "har_data": False, "varer": []}

        d = _d.fromisoformat(dato)
        iso = d.isocalendar()
        uge, aar = int(iso[1]), int(iso[0])
        dag_kol = ['man', 'tir', 'ons', 'tor', 'fre', 'loe', 'son'][d.weekday()]

        # Bestilt i dag + solgt i dag, pr. vare (matcher varenummer som spild-rapporten)
        rows = conn.execute(f"""
            SELECT b.varenummer, b.varenavn, b.{dag_kol} AS bestilt,
                   COALESCE((
                       SELECT ROUND(SUM(t.antal), 0) FROM transaktioner t
                       WHERE t.dato = ?
                         AND CAST(CAST(t.varenummer AS REAL) AS INTEGER)
                             = CAST(CAST(b.varenummer AS REAL) AS INTEGER)
                   ), 0) AS solgt
            FROM ugebestillinger b
            WHERE b.uge = ? AND b.aar = ? AND b.{dag_kol} > 0
              AND b.varenummer != '' AND b.varenummer != '0'
        """, (dato, uge, aar)).fetchall()

        # Pose-definitioner fra jeres TGTG-opsætning (navn + kreditpris)
        db_poser = conn.execute(
            "SELECT navn, kreditpris FROM tgtg_poser WHERE aktiv = 1"
        ).fetchall()

    def _find_pose(kw: str):
        """Match opskrift-nøgleord mod faktisk pose-navn i DB → (navn, kreditpris)."""
        for r in db_poser:
            nn = (r['navn'] or '').lower().replace(' ', '')
            if kw in nn:
                return r['navn'], round(r['kreditpris'] or 0, 2)
        return None, None

    varer = []
    total_retur = total_tgtg = total_rester = 0
    for r in rows:
        navn = r['varenavn'] or ''
        kat = _kat(navn)
        if kat == 'kage':
            continue  # kager: lang holdbarhed — ikke retur/TGTG samme dag
        bestilt = int(r['bestilt'] or 0)
        solgt = int(r['solgt'] or 0)
        rester = max(0, bestilt - solgt)
        if rester <= 0:
            continue
        if kat == 'wiener':
            retur_cap = _math.ceil(bestilt * RETUR_WIENER_PCT)
        elif kat == 'boller':
            retur_cap = _math.ceil(bestilt * RETUR_BOLLER_PCT)
        else:
            retur_cap = 0  # brød/andet: bageren tager ikke retur → alt til TGTG
        retur = min(rester, retur_cap)
        tgtg = rester - retur
        total_retur += retur
        total_tgtg += tgtg
        total_rester += rester
        varer.append({
            'varenummer': r['varenummer'],
            'varenavn':   navn,
            'kategori':   kat,
            'bestilt':    bestilt,
            'solgt':      solgt,
            'rester':     rester,
            'retur':      retur,
            'tgtg':       tgtg,
        })

    varer.sort(key=lambda x: -x['rester'])

    # Retur-liste: præcis hvad der sendes tilbage til bageren, pr. vare
    retur_varer = [{'varenavn': v['varenavn'], 'antal': v['retur']}
                   for v in varer if v['retur'] > 0]

    # TGTG-bare rester pr. kategori (kun brød/boller/wiener kan komme i poser)
    avail = {'brød': 0, 'boller': 0, 'wiener': 0}
    for v in varer:
        if v['kategori'] in avail:
            avail[v['kategori']] += v['tgtg']

    # Match opskrifter mod faktiske poser i DB (navn + kreditpris)
    recepter = []
    for kw, emoji, opskrift in POSE_OPSKRIFT:
        navn_db, pris = _find_pose(kw)
        if navn_db is not None:
            recepter.append({'navn': navn_db, 'emoji': emoji, 'opskrift': opskrift, 'pris': pris or 0})

    # Optimering: find antal af hver posetype der bruger FLEST rester (mindst spild),
    # tiebreak på samlet TGTG-kreditpris. Små tal → udtømmende søgning.
    def _kapacitet(rec, av):
        return min(av[k] // n for k, n in rec['opskrift'].items()) if rec['opskrift'] else 0

    best = None  # (brugte_varer, kreditpris, antal_liste)
    rng = []
    for rec in recepter:
        rng.append(range(_kapacitet(rec, avail) + 1))
    import itertools as _it
    for kombi in _it.product(*rng) if recepter else []:
        bB = bBo = bW = 0
        verdi = brugt = 0
        for antal, rec in zip(kombi, recepter):
            for k, n in rec['opskrift'].items():
                if k == 'brød':   bB  += antal * n
                if k == 'boller': bBo += antal * n
                if k == 'wiener': bW  += antal * n
            verdi += antal * rec['pris']
            brugt += antal * sum(rec['opskrift'].values())
        if bB <= avail['brød'] and bBo <= avail['boller'] and bW <= avail['wiener']:
            score = (brugt, verdi)
            if best is None or score > best[0]:
                best = (score, kombi)

    poser = []
    poser_kreditpris = 0
    brugt_kat = {'brød': 0, 'boller': 0, 'wiener': 0}
    if best:
        for antal, rec in zip(best[1], recepter):
            if antal <= 0:
                continue
            for k, n in rec['opskrift'].items():
                brugt_kat[k] += antal * n
            poser_kreditpris += antal * rec['pris']
            poser.append({
                'navn':       rec['navn'],
                'emoji':      rec['emoji'],
                'antal_poser': antal,
                'kreditpris': rec['pris'],
                'stk_pr_pose': sum(rec['opskrift'].values()),
                'indhold':    [{'kategori': _KAT_LABEL[k], 'antal': n}
                               for k, n in rec['opskrift'].items()],
            })
        poser.sort(key=lambda p: -p['antal_poser'])

    # Rester der IKKE kunne pakkes i en hel pose (ægte spild-risiko)
    pose_rest = [{'kategori': _KAT_LABEL[k], 'antal': avail[k] - brugt_kat[k]}
                 for k in ('brød', 'boller', 'wiener') if avail[k] - brugt_kat[k] > 0]

    total_poser = sum(p['antal_poser'] for p in poser)

    return {
        'dato':             dato,
        'uge':              uge,
        'aar':              aar,
        'har_data':         len(varer) > 0,
        'varer':            varer,
        'retur_varer':      retur_varer,
        'tgtg_kat':         {_KAT_LABEL[k]: avail[k] for k in avail if avail[k] > 0},
        'poser':            poser,
        'pose_rest':        pose_rest,
        'poser_kreditpris': round(poser_kreditpris, 2),
        'total_poser':      total_poser,
        'total_rester':     total_rester,
        'total_retur':      total_retur,
        'total_tgtg':       total_tgtg,
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
        "note": "Fre/Lør/Søn stiger — Søn er Pinsesøndag. Mandag (2. Pinsedag) håndteres i næste uge",
        "dag_fak": {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.10,"loe":1.20,"son":1.40},
    }

    # ── 2. Pinsedag (mandag efter pinse — Organic Market ÅBEN) ──────────
    # De fleste butikker lukket → øget trafik til de få åbne
    pinse_man = pinse + timedelta(days=1)
    pw2, py2 = _yw(pinse_man)
    if (pw2, py2) != (pw, py):
        ev[(pw2, py2)] = {
            "factor": 1.10, "navn": "2. Pinsedag — åben (de fleste lukket)",
            "note": "Mandag helligdag — en af få åbne butikker, forvent øget trafik",
            "dag_fak": {"man":1.25,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.0,"loe":1.0,"son":1.0},
        }

    # ── Grundlovsdag + Fars dag (5. juni — i Danmark fejres Fars dag på Grundlovsdag) ──
    grundlov = _date(aar, 6, 5)
    gw, gy = _yw(grundlov)
    # Grundlovsdag er typisk en fredag — stærk dag. Fars dag løfter lørdagen yderligere.
    dag_fak_gf = {"man":1.0,"tir":1.0,"ons":1.0,"tor":1.0,"fre":1.60,"loe":1.30,"son":1.10}
    if (gw, gy) not in ev:
        ev[(gw, gy)] = {
            "factor": 1.30,
            "navn": f"Grundlovsdag + Fars dag (5. jun.)",
            "note": "Fredag fridag + Fars dag — ekstra kage og brød fredag og lørdag",
            "dag_fak": dag_fak_gf,
        }
    else:
        existing = dict(ev[(gw, gy)])
        existing["dag_fak"] = dict(existing["dag_fak"])
        existing["dag_fak"]["fre"] = max(existing["dag_fak"].get("fre", 1.0), 1.60)
        existing["dag_fak"]["loe"] = max(existing["dag_fak"].get("loe", 1.0), 1.30)
        existing["factor"] = max(existing["factor"], 1.25)
        if "Grundlovsdag" not in existing.get("navn", ""):
            existing["navn"] = existing["navn"] + " + Grundlovsdag + Fars dag"
        elif "Fars dag" not in existing.get("navn", ""):
            existing["navn"] = existing["navn"] + " + Fars dag"
        ev[(gw, gy)] = existing

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
    # Wienerbrød — tjekkes FØR brød/kage så croissant/snegl ikke lander forkert
    if any(k in n for k in ('wiener', 'spandauer', 'croissant', 'snegl', 'snurrer',
                             'tebirkes', 'grovbirkes', 'fastelavns', 'wienerstang',
                             'kanelstang', 'frøsnapper', 'marcipan', 'romsnegl')):
        return 'Wiener'
    # Studenterbrød er kage trods "brød" i navnet
    if ('brød' in n or 'brod' in n) and 'studenter' not in n:
        return 'Brød'
    # Brød-varer uden 'brød' i varenavn (focaccia, formbrød mv.)
    if any(k in n for k in ('focaccia', 'foccacia', 'formbrød', 'franskbrød')):
        return 'Brød'
    # Kager — eksplicit match
    if any(k in n for k in ('kage', 'cookie', 'muffin', 'brownie', 'romkugl',
                             'kokostoppe', 'napoleonshat', 'studenterbr',
                             'snitter', 'stammer', 'honningbomb', 'honninghjerter')):
        return 'Kage'
    # Fallback
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
    """Returnerer månedlig MobilePay-omsætning — daglige data foretrækkes frem for manuelle."""
    from datetime import date as _date
    with _conn() as conn:
        # Daglige data aggregeret til måneder
        dag_rows = conn.execute("""
            SELECT CAST(strftime('%Y', dato) AS INTEGER) AS aar,
                   CAST(strftime('%m', dato) AS INTEGER) AS maaned,
                   SUM(omsaetning_inkl) AS omsaetning,
                   'dag' AS kilde
            FROM mobilepay_dag
            GROUP BY aar, maaned
        """).fetchall()
        # Manuelle månedsposter
        mnd_rows = conn.execute("""
            SELECT aar, maaned, omsaetning, 'manuel' AS kilde
            FROM mobilepay
        """).fetchall()

    merged: Dict = {}
    # Manuel data som udgangspunkt
    for r in mnd_rows:
        key = (r["aar"], r["maaned"])
        merged[key] = {"aar": r["aar"], "maaned": r["maaned"],
                       "omsaetning": r["omsaetning"], "kilde": "manuel"}
    # Daglige data overskriver manuelle (mere præcise)
    for r in dag_rows:
        key = (r["aar"], r["maaned"])
        merged[key] = {"aar": r["aar"], "maaned": r["maaned"],
                       "omsaetning": round(r["omsaetning"], 2), "kilde": "dag"}

    return sorted(merged.values(), key=lambda x: (x["aar"], x["maaned"]), reverse=True)


# ── VARESTAMDATA ──────────────────────────────────────────────────────────────

def hent_stamdata() -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT id, sku, varenavn, type, pris_ex_moms,
                   COALESCE(portioner, 1) AS portioner
            FROM varestamdata
            ORDER BY type, varenavn
        """).fetchall()
    return [dict(r) for r in rows]


def gem_stamdata_linje(sku: str, varenavn: str, type_: str, pris_ex_moms: float,
                       portioner: int = 1) -> int:
    with _conn() as conn:
        cur = conn.execute("""
            INSERT INTO varestamdata (sku, varenavn, type, pris_ex_moms, portioner)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(varenavn) DO UPDATE SET
                sku          = excluded.sku,
                type         = excluded.type,
                pris_ex_moms = excluded.pris_ex_moms,
                portioner    = excluded.portioner
        """, (sku or '', varenavn, type_, pris_ex_moms or 0, portioner or 1))
        return cur.lastrowid


def slet_stamdata(id_: int):
    with _conn() as conn:
        conn.execute("DELETE FROM varestamdata WHERE id = ?", (id_,))


def gem_stamdata_bulk(linjer: List[Dict]) -> int:
    with _conn() as conn:
        conn.executemany("""
            INSERT INTO varestamdata (sku, varenavn, type, pris_ex_moms, portioner)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(varenavn) DO UPDATE SET
                sku          = excluded.sku,
                type         = excluded.type,
                pris_ex_moms = excluded.pris_ex_moms,
                portioner    = excluded.portioner
        """, [(r.get("sku", ""), r["varenavn"], r["type"], r.get("pris_ex_moms", 0),
               r.get("portioner", 1) or 1)
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
        # ── Salgdata til vækst + TGTG (altid beregnet) ──────────────────────
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
                try:
                    dag_vals = {d: int(float(r[d] or 0)) for d in DAGE}
                except (ValueError, TypeError) as e:
                    print(f"Fejl ved konvertering af dag-værdier for {r['varenavn']}: {e}")
                    dag_vals = {d: 0 for d in DAGE}
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
            # Beregn kontekstværdier (vises som info, påvirker ikke faktisk bestilling)
            eff_f   = [(r["kasse_stk"] or 0) + round((r["tgtg_kr"] or 0) / TGTG_PR_POSE) for r in salg_rows]
            b3_f    = [v for v in eff_f[:3] if v > 0]
            p3_f    = [v for v in eff_f[3:6] if v > 0]
            bavg_f  = sum(b3_f) / len(b3_f) if b3_f else 1.0
            pavg_f  = sum(p3_f) / len(p3_f) if p3_f else bavg_f
            vaekst_f = max(-0.15, min(0.15, bavg_f / pavg_f - 1)) if pavg_f > 0 else 0.0
            tgtg_kr_f = next((r["tgtg_kr"] for r in salg_rows if (r["tgtg_kr"] or 0) > 0), 0) or 0
            si_f    = _SI_MAANED.get(mon_dato.month, 1.0)
            evt_f   = _get_event(maal_uge, maal_aar)
            return {
                "maal_uge":        maal_uge,
                "maal_aar":        maal_aar,
                "dato_range":      _dato_range(maal_uge, maal_aar),
                "basis_uge":       maal_uge,
                "basis_aar":       maal_aar,
                "maaned":          mon_dato.month,
                "si":              round(si_f, 3),
                "event":           evt_f if evt_f else None,
                "tgtg_kr":         round(tgtg_kr_f),
                "tgtg_ok":         tgtg_kr_f < 800,
                "tgtg_advarsel":   tgtg_kr_f > 1200,
                "tgtg_korrektion": 0.95 if tgtg_kr_f > 1000 else 1.0,
                "vaekst_pct":      round(vaekst_f * 100, 1),
                "total_faktor":    1.0,
                "produkter":       produkter,
                "total_stk":       total_stk,
                "total_kr":        round(total_kr, 2),
                "faktisk":         True,
            }

        # ── Ingen faktisk bestilling → beregn anbefaling ────────────────────
        # Find alle historiske bestillingsuger (robuste snit over 26+ uger)
        basis_rows = conn.execute("""
            SELECT uge, aar FROM ugebestillinger
            WHERE (aar < ? OR (aar = ? AND uge < ?))
            GROUP BY uge, aar
            ORDER BY aar DESC, uge DESC
            LIMIT 52
        """, (maal_aar, maal_aar, maal_uge)).fetchall()

        if not basis_rows:
            basis_rows = conn.execute("""
                SELECT uge, aar FROM ugebestillinger
                GROUP BY uge, aar ORDER BY aar DESC, uge DESC LIMIT 52
            """).fetchall()

        if not basis_rows:
            return {"error": "Ingen ugebestillinger indlæst endnu"}

        # Samme uge sidste år (til reference i tabellen)
        sidst_aar_rows = conn.execute("""
            SELECT varenummer, total_antal, man, tir, ons, tor, fre, loe, son
            FROM ugebestillinger WHERE uge=? AND aar=?
        """, (maal_uge, maal_aar - 1)).fetchall()
        sidst_aar_map = {r["varenummer"]: dict(r) for r in sidst_aar_rows}

        # Primær basis: seneste uge (til produkt-liste og rækkefølge)
        basis_uge = basis_rows[0]["uge"]
        basis_aar = basis_rows[0]["aar"]

        # Hent alle produkter fra basis-ugen — bevar original rækkefølge (id)
        prod_rows = conn.execute("""
            SELECT varenummer, varenavn, pris_ex_moms,
                   man, tir, ons, tor, fre, loe, son, total_antal
            FROM ugebestillinger
            WHERE uge=? AND aar=?
            ORDER BY id
        """, (basis_uge, basis_aar)).fetchall()

        # Byg snit-map over alle historiske uger pr. varenummer pr. dag (26+ ugers robust basis)
        # Bruges til at udjævne atypiske uger i selve anbefalingen
        _dag_cols = ['man','tir','ons','tor','fre','loe','son']
        _basis_snit: Dict = {}  # {varenummer: {dag: snit_antal}}
        for br in basis_rows:
            br_rows = conn.execute("""
                SELECT varenummer, man, tir, ons, tor, fre, loe, son
                FROM ugebestillinger WHERE uge=? AND aar=? ORDER BY id
            """, (br["uge"], br["aar"])).fetchall()
            for rr in br_rows:
                vn = rr["varenummer"] or ""
                if vn not in _basis_snit:
                    _basis_snit[vn] = {d: [] for d in _dag_cols}
                for d in _dag_cols:
                    if rr[d] and rr[d] > 0:
                        _basis_snit[vn][d].append(float(rr[d]))

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

        # salg_rows er allerede hentet ovenfor (delt mellem faktisk og beregnet sti)

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
        kat = _kat(r["varenavn"], sd_map)
        vn  = r["varenummer"] or ""

        # Kager: ALTID kun seneste uge, ingen gennemsnit
        if kat == 'Kage':
            basis_dag = {}
            for d in DAGE:
                try:
                    basis_dag[d] = float(r[d] or 0)
                except (ValueError, TypeError):
                    basis_dag[d] = 0.0
            anb_dag = {d: int(basis_dag[d]) for d in DAGE}
        else:
            # Andre varer: brug snit af de 3 seneste uger pr. dag (mere robust)
            vn_key = r["varenummer"] or ""
            snit_data = _basis_snit.get(vn_key, {})
            basis_dag = {}
            for d in DAGE:
                vals = snit_data.get(d, [])
                if vals:
                    basis_dag[d] = sum(vals) / len(vals)  # snit
                else:
                    try:
                        basis_dag[d] = float(r[d] or 0)       # fallback til seneste uge
                    except (ValueError, TypeError):
                        basis_dag[d] = 0.0

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

        # Samme uge sidste år for dette produkt
        sa = sidst_aar_map.get(vn, {})
        sidst_aar_total = int(sa.get("total_antal") or 0) if sa else None

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
            "sidst_aar":       sidst_aar_total,
            "sidst_aar_aar":   maal_aar - 1,
        })

    total_stk = sum(p["total_anbefalet"] for p in produkter)
    total_kr  = sum(p["total_pris"]      for p in produkter)

    return {
        "maal_uge":        maal_uge,
        "maal_aar":        maal_aar,
        "dato_range":      _dato_range(maal_uge, maal_aar),
        "basis_uge":       basis_uge,
        "basis_aar":       basis_aar,
        "basis_uger_snit": len(basis_rows),
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


# ── BASIS BESTILLING (DAGLIG SKABELON) ────────────────────────────────────────

def hent_basis_bestilling() -> List[Dict]:
    """Hent alle basis-bestillinger (produkt × dag) med vareinfo."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT bb.varenummer, bb.varenavn, bb.dag, bb.anbefalet_antal,
                   bb.kategori, bb.opdateret
            FROM basis_bestilling bb
            ORDER BY bb.varenavn, CASE bb.dag
                WHEN 'man' THEN 0 WHEN 'tir' THEN 1 WHEN 'ons' THEN 2
                WHEN 'tor' THEN 3 WHEN 'fre' THEN 4 WHEN 'loe' THEN 5
                WHEN 'son' THEN 6 ELSE 7 END
        """).fetchall()
    return [dict(r) for r in rows]


def hent_basis_bestilling_ved_dag(dag: str) -> List[Dict]:
    """Hent basis-bestillinger for en specifik ugedag."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT varenummer, varenavn, anbefalet_antal, kategori
            FROM basis_bestilling
            WHERE dag = ?
            ORDER BY varenavn
        """, (dag,)).fetchall()
    return [dict(r) for r in rows]


def gem_basis_bestilling(varenummer: str, varenavn: str, dag: str, antal: int, kategori: str = ''):
    """Gem eller opdater en basis-bestillingslinje (produkt × dag)."""
    with _conn() as conn:
        conn.execute("""
            INSERT INTO basis_bestilling (varenummer, varenavn, dag, anbefalet_antal, kategori, opdateret)
            VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(varenummer, dag) DO UPDATE SET
                anbefalet_antal = excluded.anbefalet_antal,
                varenavn = excluded.varenavn,
                kategori = excluded.kategori,
                opdateret = datetime('now','localtime')
        """, (varenummer, varenavn, dag, antal, kategori))
        conn.commit()


def slet_basis_bestilling_linje(varenummer: str, dag: str):
    """Fjern en basis-bestillingslinje."""
    with _conn() as conn:
        conn.execute("DELETE FROM basis_bestilling WHERE varenummer = ? AND dag = ?",
                    (varenummer, dag))
        conn.commit()


def bulk_opdater_basis_bestilling(updates: List[Dict]):
    """Batch-opdater flere basis-bestillinger.

    Input: [{varenummer, varenavn, dag, anbefalet_antal, kategori}, ...]
    """
    with _conn() as conn:
        for upd in updates:
            gem_basis_bestilling(
                upd['varenummer'],
                upd.get('varenavn', ''),
                upd['dag'],
                upd.get('anbefalet_antal', 0),
                upd.get('kategori', '')
            )
        conn.commit()


def hent_basis_bestilling_produkter() -> List[Dict]:
    """Hent alle unikke produkter der er i basis_bestilling med deres kategori."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT varenummer, varenavn, kategori
            FROM basis_bestilling
            ORDER BY varenavn
        """).fetchall()
    return [dict(r) for r in rows]


# ── HELLIGDAGE ────────────────────────────────────────────────────────────────

def hent_helligdage(aar: int = None) -> List[Dict]:
    """Hent alle helligdage, eventuelt filtreret efter år."""
    with _conn() as conn:
        if aar:
            rows = conn.execute(
                "SELECT dato, navn, type FROM helligdage WHERE dato LIKE ? ORDER BY dato",
                (f"{aar}-%",)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT dato, navn, type FROM helligdage ORDER BY dato"
            ).fetchall()
    return [dict(r) for r in rows]


def er_helligdag(dato: str) -> bool:
    """Check om en dato er helligdag."""
    with _conn() as conn:
        result = conn.execute(
            "SELECT 1 FROM helligdage WHERE dato = ?", (dato,)
        ).fetchone()
    return result is not None


def gem_helligdag(dato: str, navn: str, type_: str = 'normal'):
    """Gem eller opdater en helligdag."""
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO helligdage (dato, navn, type) VALUES (?, ?, ?)",
            (dato, navn, type_)
        )
        conn.commit()


# ── VF DRILL-DOWN ─────────────────────────────────────────────────────────────

def hent_vf_detaljer(aar: int, maaned: int) -> Dict:
    """Ugevis bageri-faktura + kategori-niveau andet VF for en enkelt måned."""
    from datetime import date as _date, timedelta as _td
    with _conn() as conn:
        # Hvilke ISO-uger falder i denne måned?
        # En uge tilhører den måned hvor MANDAG ligger — så hver uge tæller kun én gang.
        first = _date(aar, maaned, 1)
        last  = _date(aar, maaned + 1, 1) - _td(days=1) if maaned < 12 else _date(aar, 12, 31)

        uger_i_maaned = set()
        dag = first
        while dag <= last:
            mandag = dag - _td(days=dag.weekday())  # mandag i ugen
            sondag = mandag + _td(days=6)  # søndag i ugen
            # Inkluder uge hvis nogen dag fra ugen ligger i denne måned
            if not (sondag < first or mandag > last):
                uger_i_maaned.add((dag.isocalendar()[0], dag.isocalendar()[1]))
            dag += _td(days=1)

        # Hent bager_regnskab — fordel netto med historisk salgsfordelingsnøgle
        nøgle_vf = _dag_fordeling_nøgle()
        bager_rækker = []
        for (y, w) in sorted(uger_i_maaned):
            row = conn.execute("""
                SELECT b.uge, b.aar, b.faktura, b.retur_ialt,
                       COALESCE(u.bestilt_kr, 0) AS bestilt_kr_uge
                FROM bager_regnskab b
                LEFT JOIN (
                    SELECT uge, aar, ROUND(SUM(total_pris),2) AS bestilt_kr
                    FROM ugebestillinger GROUP BY uge, aar
                ) u ON u.uge = b.uge AND u.aar = b.aar
                WHERE b.uge=? AND b.aar=?
            """, (w, y)).fetchone()
            if not row or (row["faktura"] or 0) <= 0:
                continue

            fakt_netto = round((row["faktura"] or 0) - (row["retur_ialt"] or 0), 2)
            mon_dato = _date.fromisocalendar(y, w, 1)

            # Hent faktisk omsætning og VF per dag fra transaktioner
            # Bruges til at fordele ugens faktura efter salgsfordeling
            # ISO-uge: beregn dato-interval for ugen (mandag-søndag)
            mandag_dato = _date.fromisocalendar(y, w, 1)
            sondag_dato = mandag_dato + _td(days=6)

            dag_data = conn.execute("""
                SELECT dato,
                       ROUND(COALESCE(SUM(omsætning), 0), 2) AS omsat_inkl_dag,
                       ROUND(COALESCE(SUM(omsætning)/1.25, 0), 2) AS omsat_ex_dag,
                       ROUND(COALESCE(SUM(CASE WHEN CAST(CAST(varenummer AS REAL) AS INTEGER) IN (
                           SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                           FROM ugebestillinger WHERE varenummer != '' AND varenummer != '0'
                       ) THEN vf_korrekt ELSE 0 END), 0), 2) AS vf_dag
                FROM v_transaktioner
                WHERE dato >= ? AND dato <= ?
                GROUP BY dato ORDER BY dato
            """, (mandag_dato.isoformat(), sondag_dato.isoformat())).fetchall()

            # Byg map: dato → (omsætning inkl, omsætning ex, vf)
            dag_map = {r["dato"]: (r["omsat_inkl_dag"], r["omsat_ex_dag"], r["vf_dag"]) for r in dag_data}

            # Fordel faktura og VF efter FAKTISK SALG hver dag
            netto_maaned = 0.0
            vf_maaned = 0.0
            omsat_total = sum(v[1] for v in dag_map.values())  # ex-moms — hele ugen
            omsat_total_inkl = sum(v[0] for v in dag_map.values())  # inkl-moms — hele ugen

            for i in range(7):
                dag = mon_dato + _td(days=i)
                dag_str = dag.isoformat()
                omsat_inkl_dag, omsat_ex_dag, vf_dag = dag_map.get(dag_str, (0, 0, 0))

                if dag.month == maaned and dag.year == aar and omsat_total > 0:
                    # Fordel efter andel af samlet uge-salg
                    andel = omsat_ex_dag / omsat_total
                    netto_maaned += fakt_netto * andel
                    vf_maaned += vf_dag  # VF allerede fordelt per dag

            if netto_maaned > 0 or vf_maaned > 0:
                bestilt_andel = round((row["bestilt_kr_uge"] or 0) / 7 * sum(1 for i in range(7)
                    if (mon_dato + _td(days=i)).month == maaned and (mon_dato + _td(days=i)).year == aar), 2)
                bager_rækker.append({
                    "uge": w, "aar": y,
                    "omsat_inkl_uge": round(omsat_total_inkl, 2),
                    "omsat_ex_uge":   round(omsat_total, 2),
                    "faktura":    round(row["faktura"] or 0, 2),
                    "retur_ialt": round(row["retur_ialt"] or 0, 2),
                    "netto":      round(netto_maaned, 2),
                    "vf_maaned":  round(vf_maaned, 2),
                    "bestilt_kr": bestilt_andel,
                })

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


# ── RETUR DETALJER ────────────────────────────────────────────────────────────

def gem_retur_detaljer(uge: int, aar: int, items: list, dato: str) -> int:
    """Gemmer bekræftede retur-detaljer pr. dato.
    Sletter ALT for samme registreret_dato (uanset gemt uge/aar) så dubletter undgås.
    Uge/aar beregnes fra datoen for at sikre korrekthed."""
    from datetime import date as _d
    _parsed = _d.fromisoformat(dato)
    iso = _parsed.isocalendar()
    korrekt_uge = iso[1]
    korrekt_aar = iso[0]
    with _conn() as conn:
        conn.execute("DELETE FROM retur_detaljer WHERE registreret_dato=?", (dato,))
        for it in items:
            conn.execute(
                "INSERT INTO retur_detaljer (registreret_dato, uge, aar, produkt, antal, kategori) VALUES (?,?,?,?,?,?)",
                (dato, korrekt_uge, korrekt_aar, it['produkt'], max(0, int(it['antal'])), it.get('kategori', 'wienerbroed'))
            )
    return len(items)


def hent_retur_dage_status(uge: int, aar: int) -> list:
    """Returnerer liste over 7 dage (Man-Søn) med info om hvilke der har registrering.
    Søger på dato-interval (ikke uge-felt) så data gemt med forkert uge-nummer stadig matches."""
    from datetime import date as _date, timedelta as _td
    mandag = _date.fromisocalendar(aar, uge, 1)
    sondag = mandag + _td(days=6)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT registreret_dato FROM retur_detaljer WHERE registreret_dato >= ? AND registreret_dato <= ? ORDER BY registreret_dato",
            (mandag.isoformat(), sondag.isoformat())
        ).fetchall()
    reg_datoer = {r['registreret_dato'] for r in rows}
    dage_navne = ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn']
    result = []
    for i in range(7):
        dag = mandag + _td(days=i)
        dato_str = dag.isoformat()
        result.append({
            'dag': dage_navne[i],
            'dato': dato_str,
            'registreret': dato_str in reg_datoer,
        })
    return result


def hent_retur_uge(uge: int, aar: int) -> dict:
    """Alle retur-detaljer for en uge + kvote-beregning."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT produkt, antal, kategori, registreret_dato FROM retur_detaljer WHERE uge=? AND aar=? ORDER BY kategori, produkt",
            (uge, aar)
        ).fetchall()
        items = [dict(r) for r in rows]
        sendt_boller = sum(r['antal'] for r in items if r['kategori'] == 'boller')
        sendt_wiener = sum(r['antal'] for r in items if r['kategori'] == 'wienerbroed')
        registreret = rows[0]['registreret_dato'] if rows else None

        # Kvote fra ugebestillinger (10% boller, 13,5% wienerbrød)
        # Direkte LIKE på varenavn — varestamdata.type er 'Bagværk' for alle bestillingsvarer
        b = conn.execute("""
            SELECT COALESCE(SUM(total_antal), 0) AS t
            FROM ugebestillinger
            WHERE uge=? AND aar=?
            AND LOWER(varenavn) LIKE '%bolle%'
        """, (uge, aar)).fetchone()
        w = conn.execute("""
            SELECT COALESCE(SUM(total_antal), 0) AS t
            FROM ugebestillinger
            WHERE uge=? AND aar=?
            AND (
                LOWER(varenavn) LIKE '%croissant%' OR LOWER(varenavn) LIKE '%crossaint%' OR
                (LOWER(varenavn) LIKE '%birkes%' AND LOWER(varenavn) NOT LIKE '%hvede%') OR
                LOWER(varenavn) LIKE '%snegl%'     OR
                LOWER(varenavn) LIKE '%snurrer%'    OR LOWER(varenavn) LIKE '%snurr%'     OR
                LOWER(varenavn) LIKE '%spandauer%'  OR LOWER(varenavn) LIKE '%wienerstang%' OR
                LOWER(varenavn) LIKE '%kanelstang%' OR LOWER(varenavn) LIKE '%frøsnapper%'
            )
        """, (uge, aar)).fetchone()
        bestilt_boller = round(b['t'] or 0)
        bestilt_wiener = round(w['t'] or 0)

        return {
            'uge': uge, 'aar': aar,
            'registreret': registreret,
            'items': items,
            'sendt_boller': sendt_boller,
            'sendt_wiener': sendt_wiener,
            'bestilt_boller': bestilt_boller,
            'bestilt_wiener': bestilt_wiener,
            'max_boller': round(bestilt_boller * 0.10),
            'max_wiener': round(bestilt_wiener * 0.135),
        }


def hent_retur_kpi() -> dict:
    """KPI data til forside: seneste registrering + om aktuel uge er registreret."""
    from datetime import date, timedelta
    today = date.today()
    weekday = today.weekday()  # 0=Man, 6=Søn
    yesterday = today - timedelta(days=1)
    yesterday_iso = yesterday.isocalendar()
    aktuel_uge = int(yesterday_iso[1])
    aktuel_aar = int(yesterday_iso[0])

    # Datointerval for aktuel uge (søg på dato ikke uge-felt — JS gemte muligvis forkert uge)
    mandag_uge = date.fromisocalendar(aktuel_aar, aktuel_uge, 1)
    sondag_uge = mandag_uge + timedelta(days=6)
    mandag_str = mandag_uge.isoformat()
    sondag_str = sondag_uge.isoformat()

    with _conn() as conn:
        aktuel = conn.execute("""
            SELECT SUM(CASE WHEN kategori='boller' THEN antal ELSE 0 END) AS boller,
                   SUM(CASE WHEN kategori='wienerbroed' THEN antal ELSE 0 END) AS wiener,
                   MAX(registreret_dato) AS dato
            FROM retur_detaljer WHERE registreret_dato >= ? AND registreret_dato <= ?
        """, (mandag_str, sondag_str)).fetchone()

        seneste = conn.execute("""
            SELECT uge, aar, MAX(registreret_dato) AS dato,
                   SUM(CASE WHEN kategori='boller' THEN antal ELSE 0 END) AS boller,
                   SUM(CASE WHEN kategori='wienerbroed' THEN antal ELSE 0 END) AS wiener
            FROM retur_detaljer GROUP BY uge, aar ORDER BY aar DESC, uge DESC LIMIT 1
        """).fetchone()

        # Kvote fra ugebestillinger for aktuel uge
        # Direkte LIKE på varenavn — varestamdata.type er 'Bagværk' for alle bestillingsvarer
        b_best = conn.execute("""
            SELECT COALESCE(SUM(total_antal), 0) AS t
            FROM ugebestillinger
            WHERE uge=? AND aar=?
            AND LOWER(varenavn) LIKE '%bolle%'
        """, (aktuel_uge, aktuel_aar)).fetchone()
        w_best = conn.execute("""
            SELECT COALESCE(SUM(total_antal), 0) AS t
            FROM ugebestillinger
            WHERE uge=? AND aar=?
            AND (
                LOWER(varenavn) LIKE '%croissant%' OR LOWER(varenavn) LIKE '%crossaint%' OR
                (LOWER(varenavn) LIKE '%birkes%' AND LOWER(varenavn) NOT LIKE '%hvede%') OR
                LOWER(varenavn) LIKE '%snegl%'     OR
                LOWER(varenavn) LIKE '%snurrer%'    OR LOWER(varenavn) LIKE '%snurr%'     OR
                LOWER(varenavn) LIKE '%spandauer%'  OR LOWER(varenavn) LIKE '%wienerstang%' OR
                LOWER(varenavn) LIKE '%kanelstang%' OR LOWER(varenavn) LIKE '%frøsnapper%'
            )
        """, (aktuel_uge, aktuel_aar)).fetchone()

    bestilt_boller = round(b_best['t'] or 0)
    bestilt_wiener = round(w_best['t'] or 0)
    max_boller = round(bestilt_boller * 0.10)
    max_wiener = round(bestilt_wiener * 0.135)
    sendt_b = int(aktuel['boller'] or 0) if aktuel else 0
    sendt_w = int(aktuel['wiener'] or 0) if aktuel else 0

    er_registreret = bool(aktuel and aktuel['dato'])
    dage_status = hent_retur_dage_status(aktuel_uge, aktuel_aar)
    antal_registreret = sum(1 for d in dage_status if d['registreret'])
    return {
        'aktuel_uge': aktuel_uge,
        'aktuel_aar': aktuel_aar,
        'display_uge': aktuel_uge,
        'dage_status': dage_status,
        'antal_dage_registreret': antal_registreret,
        'er_mandag': weekday == 0,
        'er_registreret': er_registreret,
        'sendt_boller': sendt_b,
        'sendt_wiener': sendt_w,
        'registreret_dato': aktuel['dato'] if aktuel else None,
        'bestilt_boller': bestilt_boller,
        'bestilt_wiener': bestilt_wiener,
        'max_boller': max_boller,
        'max_wiener': max_wiener,
        'rest_boller': max(0, max_boller - sendt_b),
        'rest_wiener': max(0, max_wiener - sendt_w),
        'seneste_uge': int(seneste['uge']) if seneste else None,
        'seneste_aar': int(seneste['aar']) if seneste else None,
        'seneste_boller': int(seneste['boller'] or 0) if seneste else 0,
        'seneste_wiener': int(seneste['wiener'] or 0) if seneste else 0,
    }


def hent_retur_historik(n: int = 60) -> list:
    """Seneste n dage med retur-data — én post pr. registreret_dato."""
    from datetime import date as _d
    DAGE = ['mandag','tirsdag','onsdag','torsdag','fredag','lørdag','søndag']
    with _conn() as conn:
        rows = conn.execute("""
            SELECT registreret_dato AS dato,
                   SUM(CASE WHEN kategori='boller'      THEN antal ELSE 0 END) AS sendt_boller,
                   SUM(CASE WHEN kategori='wienerbroed' THEN antal ELSE 0 END) AS sendt_wiener,
                   COUNT(*) AS produkter
            FROM retur_detaljer
            GROUP BY registreret_dato
            ORDER BY registreret_dato DESC
            LIMIT ?
        """, (n,)).fetchall()

    result = []
    for r in rows:
        try:
            d   = _d.fromisoformat(r['dato'])
            iso = d.isocalendar()
        except Exception:
            continue
        result.append({
            'dato':         r['dato'],
            'ugedag':       DAGE[d.weekday()],
            'uge':          iso[1],
            'aar':          iso[0],
            'sendt_boller': r['sendt_boller'],
            'sendt_wiener': r['sendt_wiener'],
            'produkter':    r['produkter'],
        })
    return result


def hent_retur_dag(dato: str) -> dict:
    """Henter alle retur-linjer for én specifik dato."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT id, produkt, antal, kategori
            FROM retur_detaljer
            WHERE registreret_dato = ?
            ORDER BY kategori, produkt
        """, (dato,)).fetchall()
    return {'dato': dato, 'items': [dict(r) for r in rows]}


# ── SELL-THROUGH ANALYSE ─────────────────────────────────────────────────────

def hent_sellthrough_analyse(uger: int = 10) -> dict:
    """Beregner sell-through rate pr. kategori pr. ugedag over de seneste N uger.

    Matcher bestilte stk (ugebestillinger) mod solgte stk (transaktioner) via
    keyword-klassificering på varenavn — begge systemer bruger samme logik.

    Returnerer:
      sellthrough[kategori][dag] = { pct, bestilt_snit, solgt_snit, udsolgt_dage, spild_dage }
      udsolgt_dage  = dage hvor sidst-salg-time er ≥ 1 time før median lukketid
      spild_dage    = dage hvor sell-through < 75%
      tab_tabt_salg = estimeret tabt omsætning fra udsolgte dage
      tab_spild     = estimeret spild-kostpris fra overbestilte dage
    """
    from datetime import date as _d, timedelta as _td
    from collections import defaultdict

    DAGE   = ['man', 'tir', 'ons', 'tor', 'fre', 'loe', 'son']
    # SQLite strftime('%w'): 0=søn, 1=man, ..., 6=lør
    _WMAP  = {'0': 'son', '1': 'man', '2': 'tir', '3': 'ons',
               '4': 'tor', '5': 'fre', '6': 'loe'}
    KATS   = ['Boller', 'Wiener', 'Brød', 'Rugbrød', 'Kage', 'Flute']
    today  = _d.today()

    # Datointerval: de seneste N afsluttede ISO-uger
    iso_nu     = today.isocalendar()
    start_dato = (_d.fromisocalendar(iso_nu[0], iso_nu[1], 1) - _td(weeks=uger)).isoformat()
    slut_dato  = (_d.fromisocalendar(iso_nu[0], iso_nu[1], 1) - _td(days=1)).isoformat()

    with _conn() as conn:
        sd_map = _stamdata_type_map()

        # ── Bestilte stk pr. uge pr. vare ──────────────────────────────────
        best_rows = conn.execute("""
            SELECT uge, aar, varenavn, pris_ex_moms,
                   man, tir, ons, tor, fre, loe, son
            FROM ugebestillinger
            WHERE (aar > ? OR (aar = ? AND uge >= ?))
              AND (aar < ? OR (aar = ? AND uge < ?))
        """, (
            int(start_dato[:4]), int(start_dato[:4]), int(start_dato[5:7]),
            iso_nu[0], iso_nu[0], iso_nu[1]
        )).fetchall()

        # ── Solgte stk pr. dato pr. varenavn ───────────────────────────────
        salg_rows = conn.execute("""
            SELECT dato, varenavn,
                   strftime('%w', dato) AS dag_nr,
                   SUM(antal) AS solgt_stk,
                   MAX(time_start) AS sidst_time,
                   SUM(omsætning) AS solgt_oms
            FROM transaktioner
            WHERE dato >= ? AND dato <= ? AND varenavn != ''
            GROUP BY dato, varenavn
        """, (start_dato, slut_dato)).fetchall()

        # ── MobilePay pr. dato (uvarekoblet bagværkssalg) ─────────────────
        # Bruges til at estimere hvor stor en andel af salget mangler i Shopbox
        mp_rows = conn.execute("""
            SELECT dato, omsaetning_inkl / 1.25 AS oms_ex
            FROM mobilepay_dag
            WHERE dato >= ? AND dato <= ?
            ORDER BY dato
        """, (start_dato, slut_dato)).fetchall()
        mp_map = {r['dato']: float(r['oms_ex'] or 0) for r in mp_rows}

        # Total Shopbox omsætning pr. dato (til beregning af MP-andel)
        shopbox_dag = conn.execute("""
            SELECT dato, SUM(omsætning) AS oms
            FROM transaktioner WHERE dato >= ? AND dato <= ?
            GROUP BY dato
        """, (start_dato, slut_dato)).fetchall()
        shopbox_map = {r['dato']: float(r['oms'] or 0) for r in shopbox_dag}

    # ── Byg bestilt-snit pr. ISO-uge pr. kat pr. dag ──────────────────────
    # bestilt[uge_key][kat][dag] = stk
    bestilt: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    for r in best_rows:
        kat = _kat(r['varenavn'], sd_map)
        if kat not in KATS:
            continue
        uk = (r['aar'], r['uge'])
        for dag in DAGE:
            v = r[dag] or 0
            if v > 0:
                bestilt[uk][kat][dag] += v

    # ── Byg solgt pr. dato pr. kat ────────────────────────────────────────
    # solgt[(dato, kat)] = { stk, sidst_time, oms }
    solgt: dict = defaultdict(lambda: {'stk': 0, 'sidst_time': None, 'oms': 0.0})
    for r in salg_rows:
        kat = _kat(r['varenavn'], sd_map)
        if kat not in KATS:
            continue
        key = (r['dato'], kat)
        solgt[key]['stk']       += r['solgt_stk'] or 0
        solgt[key]['oms']       += r['solgt_oms'] or 0
        t = r['sidst_time']
        if t is not None and (solgt[key]['sidst_time'] is None or t > solgt[key]['sidst_time']):
            solgt[key]['sidst_time'] = t

    # ── Match bestilt vs. solgt pr. (uge, kat, dag) ───────────────────────
    # Saml pr. kat pr. dag: liste af (bestilt, solgt, sidst_time)
    obs: dict = defaultdict(lambda: defaultdict(list))  # obs[kat][dag] = [(b, s, t), ...]
    for (aar, uge), kat_dag in bestilt.items():
        try:
            man_dato = _d.fromisocalendar(aar, uge, 1)
        except Exception:
            continue
        for kat, dag_dict in kat_dag.items():
            for dag, b_stk in dag_dict.items():
                dag_idx = DAGE.index(dag)  # 0=man, 6=son
                dato    = (man_dato + _td(days=dag_idx)).isoformat()
                s_data  = solgt.get((dato, kat), {})
                s_stk   = s_data.get('stk', 0)
                s_tid   = s_data.get('sidst_time')
                obs[kat][dag].append({
                    'bestilt':   b_stk,
                    'solgt':     s_stk,
                    'sidst_tid': s_tid,
                    'dato':      dato,
                })

    # ── Beregn statistik pr. kat pr. dag ─────────────────────────────────
    DAGE_DA = {'man':'Man','tir':'Tir','ons':'Ons','tor':'Tor','fre':'Fre','loe':'Lør','son':'Søn'}
    result  = {}
    total_tab_tabt  = 0.0
    total_tab_spild = 0.0

    for kat in KATS:
        result[kat] = {}
        for dag in DAGE:
            points = obs[kat].get(dag, [])
            if len(points) < 2:
                result[kat][dag] = None
                continue

            b_vals = [p['bestilt'] for p in points if p['bestilt'] > 0]
            s_vals = [p['solgt']   for p in points if p['bestilt'] > 0]
            if not b_vals:
                result[kat][dag] = None
                continue

            b_snit = sum(b_vals) / len(b_vals)
            s_snit = sum(s_vals) / len(s_vals) if s_vals else 0
            pct    = round(s_snit / b_snit * 100) if b_snit > 0 else 0

            # Udsolgt-detektion: sidst-salg-time < 14 (kl. 14) på dage med fuld bestilling
            # Butikken lukker typisk 17-18 — hvis bagværk holder op kl. 12 er det et signal
            tider  = [p['sidst_tid'] for p in points if p['sidst_tid'] is not None and p['bestilt'] > 0]
            median_tid = sorted(tider)[len(tider)//2] if tider else None
            tidlig_stop_dage = sum(1 for p in points
                                   if p['sidst_tid'] is not None
                                   and p['sidst_tid'] < 13
                                   and p['bestilt'] > 0) if tider else 0

            udsolgt_pct  = round(tidlig_stop_dage / len(points) * 100) if points else 0
            spild_dage   = sum(1 for p in points if p['bestilt'] > 0 and (p['solgt'] / p['bestilt']) < 0.75) if b_vals else 0

            result[kat][dag] = {
                'pct':           pct,
                'bestilt_snit':  round(b_snit, 1),
                'solgt_snit':    round(s_snit, 1),
                'obs':           len(b_vals),
                'udsolgt_pct':   udsolgt_pct,     # % af uger med tidlig stop
                'spild_dage':    spild_dage,       # antal uger med <75% sell-through
                'median_tid':    median_tid,
                'signal':        'udsolgt' if pct >= 95 and udsolgt_pct >= 30 else
                                 'risiko_udsolgt' if pct >= 90 else
                                 'ok' if pct >= 75 else
                                 'spild' if pct >= 50 else 'stort_spild',
            }

    # Beregn MobilePay-andel (snit over perioden) — viser hvor meget salg mangler i Shopbox
    mp_total      = sum(mp_map.values())
    shopbox_total = sum(shopbox_map.values())
    mp_andel_pct  = round(mp_total / (shopbox_total + mp_total) * 100, 1) if (shopbox_total + mp_total) > 0 else 0

    return {
        'sellthrough':    result,
        'dage':           DAGE,
        'dage_da':        DAGE_DA,
        'kategorier':     KATS,
        'uger_analyseret': uger,
        'periode':        f"{start_dato} – {slut_dato}",
        'mp_andel_pct':   mp_andel_pct,   # % af total omsætning der er MobilePay (uvarekoblet)
        'datakvalitet': {
            'shopbox_manuelt_tastet': True,
            'mobilepay_ikke_varekoblet': True,
            'mp_andel_pct': mp_andel_pct,
            'note': f"MobilePay udgør ~{mp_andel_pct}% af omsætningen og er ikke koblet til specifikke varer. "
                    f"Reelt bagværkssalg kan være {mp_andel_pct}% højere end Shopbox-data viser."
        },
    }


# ── BESTILLINGSBEREGNER AI-KONTEKST ──────────────────────────────────────────

def generer_beregner_kontekst(maal_uge: int, maal_aar: int, api_key: str,
                               dag_totaler: dict = None, produkter: list = None,
                               vejr: dict = None) -> dict:
    """Genererer AI-hjælpetekst til bestillingsberegneren for den kommende uge."""
    from datetime import date as _d, timedelta as _td
    import anthropic as _ant

    with _conn() as conn:
        mon = _d.fromisocalendar(maal_aar, maal_uge, 1)
        sun = mon + _td(days=6)

        prev_uge = maal_uge - 1 if maal_uge > 1 else 52
        prev_aar = maal_aar if maal_uge > 1 else maal_aar - 1
        prev_mon = _d.fromisocalendar(prev_aar, prev_uge, 1)
        prev_sun = prev_mon + _td(days=6)

        # Er "forrige uge" den igangværende uge? (bestiller vi til næste uge mens indeværende ikke er slut)
        today_local = _d.today()
        prev_er_igangvaerende = prev_mon <= today_local <= prev_sun
        # Hvor mange dage er der data for i "forrige uge"?
        prev_dage_med_data = conn.execute("""
            SELECT COUNT(DISTINCT dato) AS dage, MAX(dato) AS seneste_dag
            FROM transaktioner WHERE dato>=? AND dato<=?
        """, (prev_mon.isoformat(), min(prev_sun, today_local).isoformat())).fetchone()
        prev_dage = int(prev_dage_med_data['dage'] or 0) if prev_dage_med_data else 0
        prev_seneste_dag = prev_dage_med_data['seneste_dag'] if prev_dage_med_data else None
        # Ugedagsnavn for seneste dag
        _DAGE_DA = ['mandag','tirsdag','onsdag','torsdag','fredag','lørdag','søndag']
        prev_seneste_dagsnavn = _DAGE_DA[_d.fromisoformat(prev_seneste_dag).weekday()] if prev_seneste_dag else None

        # Salg forrige uge (inkl. TGTG)
        prev_salg = conn.execute("""
            SELECT SUM(omsætning) AS oms,
                   COUNT(DISTINCT CASE WHEN bon_nr!='' THEN bon_nr END) AS kunder
            FROM transaktioner WHERE dato>=? AND dato<=?
        """, (prev_mon.isoformat(), prev_sun.isoformat())).fetchone()

        # TGTG seneste 4 uger — kr og estimeret stk
        tgtg_rows = conn.execute("""
            SELECT strftime('%Y-%W', dato) AS yw,
                   SUM(tgtg_kr) AS kr, SUM(tgtg_stk) AS stk
            FROM (
                SELECT dato,
                       SUM(CASE WHEN LOWER(varenavn) LIKE '%tgtg%' OR LOWER(varenavn) LIKE '%too good%'
                                THEN omsætning ELSE 0 END) AS tgtg_kr,
                       COUNT(CASE WHEN LOWER(varenavn) LIKE '%tgtg%' OR LOWER(varenavn) LIKE '%too good%'
                                  THEN 1 END) AS tgtg_stk
                FROM transaktioner WHERE dato >= date(?,'-28 days') AND dato <= ?
                GROUP BY dato
            ) GROUP BY yw ORDER BY yw DESC LIMIT 4
        """, (mon.isoformat(), prev_sun.isoformat())).fetchall()

        # TGTG fra tgtg_dagssalg (D+1 offset: poser afhentes dagen efter produktion)
        # Forrige uges produktioner → TGTG solgt man-søn = tirsdag (uge start+1) til mandag (næste uge)
        tgtg_dag = conn.execute("""
            SELECT SUM(antal) AS poser, SUM(kreditering) AS kr
            FROM tgtg_dagssalg
            WHERE dato >= date(?, '+1 day') AND dato <= date(?, '+1 day')
        """, (prev_mon.isoformat(), prev_sun.isoformat())).fetchone()

        # Retur forrige uge pr. produkt
        retur_varer = conn.execute("""
            SELECT produkt, SUM(antal) AS antal, kategori
            FROM retur_detaljer WHERE registreret_dato>=? AND registreret_dato<=?
            GROUP BY produkt, kategori ORDER BY antal DESC
        """, (prev_mon.isoformat(), prev_sun.isoformat())).fetchall()

        # Retur seneste 4 uger snit
        retur_snit = conn.execute("""
            SELECT AVG(b) AS snit_b, AVG(w) AS snit_w FROM (
                SELECT strftime('%Y-%W', registreret_dato) AS yw,
                       SUM(CASE WHEN kategori='boller' THEN antal ELSE 0 END) AS b,
                       SUM(CASE WHEN kategori='wienerbroed' THEN antal ELSE 0 END) AS w
                FROM retur_detaljer WHERE registreret_dato >= date(?,'-28 days')
                GROUP BY yw ORDER BY yw DESC LIMIT 4
            )
        """, (mon.isoformat(),)).fetchone()

        # Bestilling forrige uge
        best_prev = conn.execute("""
            SELECT varenavn, total_antal FROM ugebestillinger
            WHERE uge=? AND aar=? ORDER BY total_antal DESC LIMIT 12
        """, (prev_uge, prev_aar)).fetchall()

        # Dag×time heatmap — trafik-profil over seneste 8 uger
        _DAG_NAVNE_KORT = {0:'søn',1:'man',2:'tir',3:'ons',4:'tor',5:'fre',6:'lør'}
        heatmap_rows = conn.execute("""
            SELECT CAST(strftime('%w', dato) AS INTEGER) AS ugedag,
                   time_start AS time,
                   ROUND(AVG(dag_time_oms),0) AS snit_oms
            FROM (
                SELECT dato, time_start,
                       SUM(omsætning) AS dag_time_oms
                FROM transaktioner
                WHERE dato >= date(?, '-56 days') AND dato < ?
                  AND time_start BETWEEN 6 AND 19
                GROUP BY dato, time_start
            )
            GROUP BY ugedag, time_start
            ORDER BY ugedag, time_start
        """, (mon.isoformat(), mon.isoformat())).fetchall()

        # Byg komprimeret profil: top-tider per dag + relativt styrke-indeks
        from collections import defaultdict
        dag_time_map = defaultdict(dict)
        for r in heatmap_rows:
            dag_time_map[r['ugedag']][r['time']] = float(r['snit_oms'] or 0)

        dag_profil_str = ""
        if dag_time_map:
            dag_linjer = []
            # Beregn total per dag (0=søn..6=lør → konverter til man=1..søn=7)
            dag_totaler_hm = {}
            for wd in range(7):
                dag_totaler_hm[wd] = sum(dag_time_map[wd].values())
            max_dag = max(dag_totaler_hm.values()) if dag_totaler_hm else 1
            DAG_ISO = [1,2,3,4,5,6,0]  # man,tir,ons,tor,fre,lør,søn i ISO ordre
            DAG_NAVN_ISO = ['Man','Tir','Ons','Tor','Fre','Lør','Søn']
            for iso_i, (wd, dn) in enumerate(zip(DAG_ISO, DAG_NAVN_ISO)):
                if not dag_time_map[wd]:
                    continue
                dag_tot = dag_totaler_hm[wd]
                styrke = round(dag_tot / max_dag * 100)
                bar = '█' * (styrke // 10) + '░' * (10 - styrke // 10)
                # Top 3 timer
                top_timer = sorted(dag_time_map[wd].items(), key=lambda x: -x[1])[:3]
                top_str = ', '.join([f"kl.{t:02d}" for t,_ in top_timer])
                dag_linjer.append(f"  {dn}: {bar} {styrke:3d}%  (top: {top_str})")
            dag_profil_str = '\n'.join(dag_linjer)

        # Sidst-solgt tidspunkt per vare (seneste 4 uger) — indikator for udsolgt vs. overskud
        # Lukketid er typisk kl. 18. Sidst solgt kl. 11 = sandsynligvis udsolgt tidligt.
        sidst_solgt_rows = conn.execute("""
            SELECT varenavn,
                   ROUND(AVG(sidst_time), 0) AS snit_sidst_time,
                   COUNT(DISTINCT dato) AS dage_med_salg
            FROM (
                SELECT dato, varenavn, MAX(time_start) AS sidst_time
                FROM transaktioner
                WHERE dato >= date(?, '-28 days') AND dato < ?
                  AND time_start BETWEEN 6 AND 19
                  AND antal > 0
                GROUP BY dato, varenavn
            )
            GROUP BY varenavn
            HAVING dage_med_salg >= 3
            ORDER BY snit_sidst_time ASC
        """, (mon.isoformat(), mon.isoformat())).fetchall()
        # Butik lukker kl. 20 — ubemandet.
        # Sidst solgt < 14 = udsolgt tidligt (tabt salg 14-20). > 18 = overskud tæt på lukketid.
        udsolgt_tidligt = [r for r in sidst_solgt_rows if r['snit_sidst_time'] is not None and r['snit_sidst_time'] < 14]
        overskud_sent   = [r for r in sidst_solgt_rows if r['snit_sidst_time'] is not None and r['snit_sidst_time'] > 18]

        # Trend 8 uger
        trend_rows = conn.execute("""
            SELECT MIN(dato) AS uge_start, SUM(omsætning) AS oms
            FROM transaktioner WHERE dato < ?
            GROUP BY strftime('%Y-%W', dato)
            ORDER BY dato DESC LIMIT 8
        """, (mon.isoformat(),)).fetchall()

        # Salg samme periode 4 uger siden — kun samme antal dage for fair sammenligning
        prev4_mon = mon - _td(weeks=4)
        # Hvis forrige uge er igangværende: sammenlign kun de dage vi har data for
        prev4_slut = prev4_mon + _td(days=max(prev_dage - 1, 0)) if prev_er_igangvaerende and prev_dage > 0 else prev4_mon + _td(days=6)
        prev4_oms = conn.execute("""
            SELECT SUM(omsætning) AS oms FROM transaktioner WHERE dato>=? AND dato<=?
        """, (prev4_mon.isoformat(), prev4_slut.isoformat())).fetchone()

    # Events
    evt      = _get_event(maal_uge, maal_aar)
    evt_prev = _get_event(prev_uge, prev_aar)

    # Beregn tal
    prev_oms   = round(prev_salg['oms'] or 0) if prev_salg else 0
    prev_kunder= int(prev_salg['kunder'] or 0) if prev_salg else 0
    oms_4u_ago = round(prev4_oms['oms'] or 0) if prev4_oms else 0
    trend_str  = ' → '.join([f"U{r['uge_start'][5:7]}/{r['uge_start'][:4]}: {round(r['oms']):,}kr" for r in trend_rows[:6]]) if trend_rows else 'ingen data'

    # TGTG forrige uge
    tgtg_poser = int(tgtg_dag['poser'] or 0) if tgtg_dag else 0
    tgtg_kr    = round(tgtg_dag['kr'] or 0) if tgtg_dag else 0
    tgtg_snit  = round(sum(r['kr'] or 0 for r in tgtg_rows) / max(len(tgtg_rows),1)) if tgtg_rows else 0

    # Retur forrige uge
    retur_b = sum(r['antal'] for r in retur_varer if r['kategori']=='boller')
    retur_w = sum(r['antal'] for r in retur_varer if r['kategori']=='wienerbroed')
    retur_varer_str = ', '.join([f"{r['produkt']} {r['antal']} stk" for r in retur_varer[:8]]) or 'ingen registreret'
    snit_b  = round(retur_snit['snit_b'] or 0) if retur_snit else 0
    snit_w  = round(retur_snit['snit_w'] or 0) if retur_snit else 0

    best_str = ', '.join([f"{r['varenavn']} {r['total_antal']}stk" for r in best_prev[:8]]) if best_prev else 'ingen data'

    evt_info = ''
    if evt:
        dag_fak  = evt.get('dag_fak', {})
        # Beregn faktiske datoer for ugedagene så AI kan nævne dem eksplicit
        _DAG_KEYS2 = ['man','tir','ons','tor','fre','loe','son']
        _DAG_DA2   = ['mandag','tirsdag','onsdag','torsdag','fredag','lørdag','søndag']
        dag_str_list = []
        for dk, dn in zip(_DAG_KEYS2, _DAG_DA2):
            fak = dag_fak.get(dk, 1.0)
            if fak != 1.0:
                dag_dato_str = (mon + _td(days=_DAG_KEYS2.index(dk))).strftime('%-d/%-m')
                dag_str_list.append(f"{dn} {dag_dato_str}: ×{fak}")
        dag_str  = ', '.join(dag_str_list)
        evt_info = f"{evt['navn']} — faktor ×{evt['factor']} ({evt.get('note','')}).\nDag-faktorer med dato: {dag_str}"
    else:
        evt_info = 'Ingen kendte begivenheder'

    evt_prev_info = f" (BEMÆRK: forrige uge havde {evt_prev['navn']} — tallene kan være atypiske)" if evt_prev else ''

    # Byg dagsmængde-sektion
    DAG_NAVNE_DA = ["Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn"]
    DAG_KEYS     = ["man", "tir", "ons", "tor", "fre", "loe", "son"]

    # Filtrer kager fra - konstant leverance, intet spild, irrelevant for AI-vurdering
    _KAGE_KAT = {"kage", "kager"}
    produkter_uden_kage = [
        p for p in (produkter or [])
        if p.get("kategori", "").lower() not in _KAGE_KAT
    ]

    # Dag-totaler uden kager (genberegn fra produktlisten)
    dag_maengde_str = ""
    if dag_totaler and produkter_uden_kage:
        # Træk kage-mængder fra dag-totalerne
        kage_dag: Dict = {dk: 0 for dk in DAG_KEYS}
        for p in (produkter or []):
            if p.get("kategori", "").lower() in _KAGE_KAT:
                for dk in DAG_KEYS:
                    kage_dag[dk] += p.get(dk, 0)
        linjer = []
        for dk, dn in zip(DAG_KEYS, DAG_NAVNE_DA):
            total = dag_totaler.get(dn, dag_totaler.get(dk, 0))
            total_uden = total - kage_dag.get(dk, 0)
            if total_uden > 0:
                linjer.append(f"  {dn}: {total_uden} stk")
        dag_maengde_str = "\n".join(linjer) if linjer else "  (ingen data)"
    elif dag_totaler:
        linjer = []
        for dk, dn in zip(DAG_KEYS, DAG_NAVNE_DA):
            total = dag_totaler.get(dn, dag_totaler.get(dk, 0))
            if total:
                linjer.append(f"  {dn}: {total} stk")
        dag_maengde_str = "\n".join(linjer) if linjer else "  (ingen data)"

    produkt_str = ""
    if produkter_uden_kage:
        linjer = []
        for p in produkter_uden_kage[:20]:
            navn = p.get("varenavn", p.get("navn", "?"))
            kat  = p.get("kategori", "")
            dage = []
            for dk, dn in zip(DAG_KEYS, DAG_NAVNE_DA):
                v = p.get(dk, p.get("dag_val", {}).get(dk, 0))
                if v: dage.append(f"{dn}:{v}")
            total = p.get("total", sum(p.get(dk, 0) for dk in DAG_KEYS))
            linjer.append(f"  {navn} ({kat}): {' '.join(dage)} = {total} stk")
        produkt_str = "\n".join(linjer)

    vejr_str = ""
    if vejr and vejr.get("forecast"):
        fc = vejr["forecast"]
        linjer = []
        for i in range(7):
            dag = mon + _td(days=i)
            ds  = dag.isoformat()
            dn  = DAG_NAVNE_DA[i]
            dato_kort = dag.strftime('%-d/%-m')
            v   = fc.get(ds)
            if v:
                j    = v.get("juster", {})
                prec = v.get('prec', 0)
                tmax = v.get('tmax', '?')
                ikon = v.get('ikon', '')
                linje = f"  {dn} ({dato_kort}): {ikon} {tmax}°C, nedbør {prec}mm"
                if j.get("farve") == "red":
                    linje += f"  ← DÅRLIGT VEJR: {j.get('label','')} (reducer bestilling)"
                elif j.get("farve") == "orange":
                    linje += f"  ← REGN: {j.get('label','')} (overvej reduktion)"
                elif j.get("farve") == "green":
                    linje += f"  ← GODT VEJR: {j.get('label','')} (overvej ekstra)"
                else:
                    linje += "  (normalt)"
                linjer.append(linje)
            else:
                linjer.append(f"  {dn} ({dato_kort}): ingen vejrdata")
        # Tilføj eksplicit opsummering så AI ikke forveksler dage
        regn_dage = [l.split(':')[0].strip() for l in linjer if '← REGN' in l or '← DÅRLIGT VEJR' in l]
        godt_dage = [l.split(':')[0].strip() for l in linjer if '← GODT VEJR' in l]
        opsummering = "\n  VEJR-OPSUMMERING:"
        if regn_dage:
            opsummering += f"\n  • REGN/DÅRLIGT VEJR (reducer bestilling): {', '.join(regn_dage)}"
        if godt_dage:
            opsummering += f"\n  • GODT VEJR (overvej ekstra): {', '.join(godt_dage)}"
        if not regn_dage and not godt_dage:
            opsummering += "\n  • Normalt vejr hele ugen"
        vejr_str = "\n".join(linjer) + opsummering if linjer else "  Ingen vejrdata"
    else:
        vejr_str = "  Ikke tilgængelig — vejrdata ikke indlæst"

    # Sæsonindeks fra evt
    si_info = ""
    if evt:
        si_info = f"Sæsonindeks: ×{evt.get('factor', 1.0):.2f}"

    # Find ugedag for torsdag i mål-ugen (bestillingsdeadline er FORRIGE torsdag)
    tor_deadline = (mon - _td(days=4)).strftime('%-d. %B')  # torsdagen ugen før

    prompt = f"""Du er indkøbsrådgiver for Organic Market Greve — en franchise-butik i Greve, Danmark.

═══ FORRETNINGSMODEL ═══
Organic Market er FRANCHISE-TAGER og driver IKKE eget bageri.
Bagværk bestilles hos franchise-bageriet og leveres HVER MORGEN KL. 05:00.

BUTIK:
• Organic Market Greve er UBEMANDET og SELVBETJENING
• Åbningstid: kl. 06:00 – 20:00 (åbner/lukker automatisk)
• Ingen personale → ingen manuel justering eller fjernelse af varer i åbningstiden
• Friske produkter leveres kl. 05:00 og skal holde fra 06:00 til 20:00

BESTILLINGSPROCES:
• Deadline: senest TORSDAG for HELE den efterfølgende uge (man–søn)
• Du angiver mængde per dag i bestillingen
• Levering sker dagligt kl. 05:00 baseret på din fordeling
• Du kan IKKE ændre bestillingen midt i ugen

ØKONOMI:
• For meget → noget sælges via TGTG (Too Good To Go) som pose → delvis dækning
• Overskud sendes retur til bageriet → krediteres på næste faktura (boller 10%, wienerbrød 13,5%)
• For lidt → tomme hylder → tabt salg + skuffede kunder → direkte tab
• Kager: leveres fast 2×/uge i aftalt mængde — analyser dem ikke medmindre begivenhed tilsiger extra.

MÅL: Bestil præcis nok per dag — minimér både tomme hylder OG overskud.
TGTG-mål: under 800 kr/uge (= acceptabelt overskudsniveau).

PRODUKTREGLER — VIGTIGT FOR BESTILLINGEN:
┌─────────────────────────────────────────────────────────────────┐
│ RUGBRØD         → kan stå til næste dag. Lav spildrisiko.       │
│                   Kan bestilles med lidt margin.                 │
│                                                                  │
│ BOLLER          → skal sælges samme dag. Retur til bageriet     │
│                   (10% krediteres) ELLER TGTG.                  │
│                   OBS: I ender TYPISK med for mange boller.     │
│                   Vær konservativ, særligt svage dage.          │
│                                                                  │
│ BRØD (surdej,   → KAN IKKE returneres til bageriet.             │
│  flute, focac.) → Kun TGTG eller kasseres = fuldt tab.          │
│                   Vær EKSTRA konservativ. Hellere lidt for lidt. │
│                   OBS: I ender TYPISK med for meget brød.       │
│                                                                  │
│ GROV TEBIRKES   → KAN IKKE returneres til bageriet.             │
│ FRØSNAPPER      → Kun TGTG eller kasseres = fuldt tab.          │
│ HØJ KANELSNEGL  → KAN IKKE returneres (med creme).              │
│ ROSINBOLLER     → KAN IKKE returneres.                          │
│                   Alle fire: bestil kun hvad du er sikker på.   │
│                                                                  │
│ WIENERBRØD      → Retur til bageriet (13,5% krediteres)         │
│  (øvrige)         ELLER TGTG. Normal spildhåndtering.           │
└─────────────────────────────────────────────────────────────────┘

⚠ DATAKVALITET:
• Shopbox undervurderer reelt salg (MobilePay ikke varekoblet — typisk bagværk ved bordet).
• TGTG og retur er de mest præcise spild-indikatorer.
• UGEBESTILLINGER tastes MANUELT af personale — kan indeholde tastefejl.
  Hvis en dags bestilling ser urealistisk ud (fx 200 boller en mandag eller 0 fredag),
  er det sandsynligvis en tastefejl — ikke et reelt mønster. Brug historisk snit til korrektion.
═══════════════════════

─── BESTILLINGSUGE {maal_uge}/{maal_aar}: {mon.strftime('%-d. %B')} – {sun.strftime('%-d. %B %Y')} ───
Bestillingsdeadline: torsdag {tor_deadline} (bestil for hele denne uge)
{si_info}

BEGIVENHED: {evt_info}

VEJR UGE {maal_uge} (alle 7 dage du bestiller til):
{vejr_str}

─── FORESLÅEDE DAGSMÆNGDER (systemets beregning til din torsdags-bestilling) ───
Dagstotaler (excl. kager):
{dag_maengde_str if dag_maengde_str else '  (ikke tilgængelig — klik Opdater analyse efter tabellen er indlæst)'}

Pr. produkt:
{produkt_str if produkt_str else '  (ikke tilgængelig)'}

─── HISTORIK (basis for din vurdering) ───
FORRIGE UGE ({prev_uge}/{prev_aar}, {prev_mon.strftime('%-d. %b')}–{prev_sun.strftime('%-d. %b')}){evt_prev_info}:
{'⚠ IGANGVÆRENDE — kun ' + str(prev_dage) + ' dage (til ' + (prev_seneste_dagsnavn or '?') + ')' if prev_er_igangvaerende else f'{prev_dage} dage med data'}
  Omsætning: {prev_oms:,} kr · Bestilling: {best_str}

RETUR TIL BAGERIET FORRIGE UGE: {retur_b} boller + {retur_w} wienerbrød ({retur_varer_str})
Snit 4 uger: {snit_b} boller + {snit_w} wienerbrød returneret

TGTG FORRIGE UGE: {tgtg_poser} poser · {tgtg_kr:,} kr (4-ugers snit: {tgtg_snit:,} kr · mål: <800 kr)

SALGSTREND: {trend_str}

─── TRAFIK-PROFIL: DAG × KLOKKETIME (seneste 8 uger, butik 06-20) ───
{dag_profil_str if dag_profil_str else '  (ingen data)'}

─── SALGSMØNSTER: HVORNÅR STOPPER VI MED AT SÆLGE? (seneste 4 uger) ───
{'SÆLGER UD TIDLIGT — tomme hylder i timevis (sidst solgt FØR kl. 14, butik åben til 20):' + chr(10) + chr(10).join(f'  {r["varenavn"]}: sidst solgt kl. {int(r["snit_sidst_time"]):02d}:00 → {20-int(r["snit_sidst_time"])} timers tomme hylder ({r["dage_med_salg"]} dage)' for r in udsolgt_tidligt[:8]) if udsolgt_tidligt else '  Ingen varer der konsekvent sælger ud for tidligt'}

{'TYPISK OVERSKUD VED LUKKETID (sidst solgt EFTER kl. 18 — varer tæt på lukketid kl. 20):' + chr(10) + chr(10).join(f'  {r["varenavn"]}: sidst solgt kl. {int(r["snit_sidst_time"]):02d}:00 ({r["dage_med_salg"]} dage)' for r in overskud_sent[:8]) if overskud_sent else '  Ingen varer med konsekvent overskud ved lukketid'}

─── DIN BESTILLINGSOPGAVE (4 afsnit) ───
Du skal hjælpe med at beslutte TORSDAGENS bestilling for hele næste uge.

1. DAGSVURDERING — er de foreslåede dagsmængder rigtige?
   VIGTIGT: For HVER dag der har vejrjustering (se VEJR-sektionen ovenfor), SKAL du nævne:
   - Nedbørsmængden (mm)
   - Den anbefalede % justering
   - Det konkrete justerede antal stk
   Skriv ALDRIG "normalt vejr" på en dag der har nedbør >1mm — brug de faktiske vejrtal.
   Format: "Man {dag_totaler.get('Man', dag_totaler.get('man','?')) if dag_totaler else '?'} stk — [vurdering inkl. vejrtal hvis regn]"

2. VEJR & BEGIVENHED — hvilke dage i den kommende uge kræver særlig opmærksomhed?
   Regn reducerer kundeflow. Begivenheder kan løfte markant. Vær specifik.

3. RETUR & TGTG — brug produktreglerne aktivt:
   • Brød / grov tebirkes / frøsnapper / høj kanelsnegl (creme) / rosinboller: KAN IKKE returneres → overskud = fuldt tab.
     Hvis disse ender i TGTG konsekvent → reducer bestillingen næste uge.
   • Boller: retur-mulighed, men 10% kreditering er ikke gratis.
   • Rugbrød: kan stå til næste dag — lav spildrisiko.

4. BESTILLINGSANBEFALING — hvad justeres inden torsdagens bestilling?
   Prioritér produkter UDEN retur-mulighed højest (brød, grov tebirkes, frøsnapper).
   Format: "Fre brød: -3 stk (ingen retur — ender som fuldt tab ved TGTG)"
            "Man boller: -8 stk (svag dag, historisk for mange)"
            "Lør surdejsboller: +5 stk (stærk dag + godt vejr — risiko for udsolgt)"

Skriv på dansk. Vær KONKRET med tal og dagenavne. Husk: én bestilling, hele ugen. Max 400 ord."""

    client = _ant.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}]
    )
    tekst = msg.content[0].text.strip()

    return {
        "ok":         True,
        "tekst":      tekst,
        "maal_uge":   maal_uge,
        "maal_aar":   maal_aar,
        "dato_range": f"{mon.strftime('%-d. %b')} – {sun.strftime('%-d. %b %Y')}",
        "prev_uge":   prev_uge,
        "prev_oms":   prev_oms,
        "evt":        evt['navn'] if evt else None,
        "tgtg_kr":    tgtg_kr,
        "retur_b":    retur_b,
        "retur_w":    retur_w,
    }


# ── MANAGEMENT REVIEW ────────────────────────────────────────────────────────

def hent_management_data(uge: int = None, aar: int = None) -> dict:
    """Samler detaljerede KPI-data til management review for en specifik uge eller nuværende uge."""
    from datetime import date, timedelta
    from collections import defaultdict
    today = date.today()

    # Hvis uge/år ikke er angivet, brug nuværende uge
    if uge is None or aar is None:
        uge = int(today.strftime('%W')) + 1  # ISO week (0-based, så +1)
        aar = today.year

    # Find dato-interval for den ISO-uge
    # ISO-uge starter mandag, slutter søndag
    jan4 = date(aar, 1, 4)
    week1_monday = jan4 - timedelta(days=jan4.weekday())
    target_monday = week1_monday + timedelta(weeks=uge - 1)
    target_sunday = target_monday + timedelta(days=6)

    # Sikr at datoerne ikke går uden for året
    year_start = date(aar, 1, 1)
    year_end = date(aar, 12, 31)
    target_monday = max(target_monday, year_start)
    target_sunday = min(target_sunday, year_end)

    with _conn() as conn:
        # Ugentlig omsætning + DB + kunder omkring den valgte uge (+-5 uger kontekst)
        start_context = target_monday - timedelta(weeks=5)
        end_context = target_sunday + timedelta(weeks=5)
        uger_iso = conn.execute("""
            SELECT
                CAST(strftime('%Y', dato) AS INTEGER) AS aar,
                CAST(strftime('%W', dato) AS INTEGER)+1 AS uge,
                MIN(dato) AS fra, MAX(dato) AS til,
                ROUND(SUM(omsætning),0) AS oms,
                ROUND(SUM(avance),0) AS db_kr,
                ROUND(SUM(avance)*100.0/NULLIF(SUM(omsætning),0),1) AS db_pct,
                COUNT(DISTINCT dato) AS dage,
                COUNT(DISTINCT CASE WHEN bon_nr!='' THEN bon_nr END) AS kunder
            FROM transaktioner
            WHERE dato >= ? AND dato <= ?
            GROUP BY strftime('%Y-%W', dato)
            ORDER BY dato DESC LIMIT 15
        """, (start_context.isoformat(), end_context.isoformat())).fetchall()

        # Dagdata for den valgte uge
        uge_data = conn.execute("""
            SELECT dato, SUM(omsætning) AS oms, SUM(avance) AS db_kr,
                   ROUND(SUM(avance)*100.0/NULLIF(SUM(omsætning),0),1) AS db_pct,
                   COUNT(DISTINCT CASE WHEN bon_nr!='' THEN bon_nr END) AS kunder
            FROM transaktioner
            WHERE dato >= ? AND dato <= ?
            GROUP BY dato ORDER BY dato
        """, (target_monday.isoformat(), target_sunday.isoformat())).fetchall()

        # Seneste salgsdag (for sammenligning)
        seneste_dato = None
        seneste_dag = None
        prev_dag_oms = None

        # Hvis vi analyserer den valgte uge, brug sidste dag i den uge. Ellers brug seneste dag totalt
        if uge_data:
            seneste_dato = uge_data[-1]['dato']  # Sidste dag i den valgte uge
        else:
            seneste_dato = conn.execute("SELECT MAX(dato) FROM transaktioner").fetchone()[0]

        if seneste_dato:
            seneste_dag = conn.execute("""
                SELECT ROUND(SUM(omsætning),0) AS oms,
                       ROUND(SUM(avance)*100.0/NULLIF(SUM(omsætning),0),1) AS db_pct,
                       COUNT(DISTINCT CASE WHEN bon_nr!='' THEN bon_nr END) AS kunder
                FROM transaktioner WHERE dato=?
            """, (seneste_dato,)).fetchone()
            # Samme dag forrige uge
            prev_dag = (target_sunday - timedelta(weeks=1)).isoformat() if target_sunday else None
            if prev_dag:
                prev_dag_oms = conn.execute(
                    "SELECT ROUND(SUM(omsætning),0) AS oms FROM transaktioner WHERE dato=?",
                    (prev_dag,)
                ).fetchone()

        # Kategorier i den valgte uge med vaekst vs samme uge året før
        prev_monday = target_monday - timedelta(weeks=52)
        prev_sunday = target_sunday - timedelta(weeks=52)
        kat_nu = conn.execute("""
            SELECT kategori, ROUND(SUM(omsætning),0) AS oms,
                   ROUND(SUM(avance)*100.0/NULLIF(SUM(omsætning),0),1) AS db_pct
            FROM transaktioner
            WHERE dato >= ? AND dato <= ? AND kategori != ''
            GROUP BY kategori ORDER BY oms DESC LIMIT 10
        """, (target_monday.isoformat(), target_sunday.isoformat())).fetchall()
        kat_prev = {r['kategori']: r['oms'] for r in conn.execute("""
            SELECT kategori, ROUND(SUM(omsætning),0) AS oms FROM transaktioner
            WHERE dato >= ? AND dato <= ? AND kategori != ''
            GROUP BY kategori
        """, (prev_monday.isoformat(), prev_sunday.isoformat())).fetchall()}

        # Dag-af-uge snit, 12 uger omkring den valgte uge
        dag_snit = conn.execute("""
            SELECT
                CASE CAST(strftime('%w',dato) AS INTEGER)
                    WHEN 1 THEN 'Mandag' WHEN 2 THEN 'Tirsdag' WHEN 3 THEN 'Onsdag'
                    WHEN 4 THEN 'Torsdag' WHEN 5 THEN 'Fredag' WHEN 6 THEN 'Lordag'
                    ELSE 'Sondag' END AS dag,
                CAST(strftime('%w',dato) AS INTEGER) AS dag_nr,
                ROUND(AVG(dag_oms),0) AS snit_oms,
                ROUND(AVG(dag_kunder),0) AS snit_kunder,
                COUNT(*) AS uger
            FROM (
                SELECT dato, SUM(omsætning) AS dag_oms,
                       COUNT(DISTINCT CASE WHEN bon_nr!='' THEN bon_nr END) AS dag_kunder
                FROM transaktioner WHERE dato >= ? AND dato <= ?
                GROUP BY dato
            ) GROUP BY dag_nr ORDER BY dag_nr
        """, (start_context.isoformat(), end_context.isoformat())).fetchall()

        # Top 15 produkter i den valgte uge + sammenligningperiode (samme uge året før)
        top_nu = conn.execute("""
            SELECT varenavn, ROUND(SUM(omsætning),0) AS oms, SUM(antal) AS antal,
                   ROUND(SUM(avance)*100.0/NULLIF(SUM(omsætning),0),1) AS db_pct
            FROM transaktioner
            WHERE dato >= ? AND dato <= ? AND varenavn != ''
            GROUP BY varenavn ORDER BY oms DESC LIMIT 15
        """, (target_monday.isoformat(), target_sunday.isoformat())).fetchall()
        top_prev_map = {r['varenavn']: r['oms'] for r in conn.execute("""
            SELECT varenavn, ROUND(SUM(omsætning),0) AS oms FROM transaktioner
            WHERE dato >= ? AND dato <= ? AND varenavn != ''
            GROUP BY varenavn
        """, (prev_monday.isoformat(), prev_sunday.isoformat())).fetchall()}

        # TGTG omkring den valgte uge (+-5 uger kontekst)
        tgtg_uger = conn.execute("""
            SELECT strftime('%Y-%W', ds.dato) AS yw, MIN(ds.dato) AS fra,
                   SUM(ds.antal) AS poser,
                   ROUND(SUM(ds.antal * COALESCE(tp.kreditpris,
                       CASE WHEN ds.kreditering > 0 THEN ds.kreditering / ds.antal ELSE 0 END, 0)), 0) AS kr
            FROM tgtg_dagssalg ds
            LEFT JOIN tgtg_poser tp ON ds.item_id = tp.item_id OR ds.pose_navn = tp.navn
            WHERE ds.dato >= ? AND ds.dato <= ?
            GROUP BY yw ORDER BY yw DESC LIMIT 8
        """, (start_context.isoformat(), end_context.isoformat())).fetchall()

        # Retur omkring den valgte uge
        retur_start = start_context
        retur_end = end_context
        retur_uger = conn.execute("""
            SELECT registreret_dato, kategori, SUM(antal) AS antal
            FROM retur_detaljer WHERE registreret_dato >= ? AND registreret_dato <= ?
            GROUP BY registreret_dato, kategori ORDER BY registreret_dato DESC
        """, (retur_start.isoformat(), retur_end.isoformat())).fetchall()
        retur_varer = conn.execute("""
            SELECT produkt, kategori, SUM(antal) AS antal
            FROM retur_detaljer WHERE registreret_dato >= ? AND registreret_dato <= ?
            GROUP BY produkt, kategori ORDER BY antal DESC LIMIT 12
        """, (target_monday.isoformat(), target_sunday.isoformat())).fetchall()

        # Bestilling for den valgte uge + næste uge
        best_nu = conn.execute(
            "SELECT varenavn, total_antal FROM ugebestillinger WHERE uge=? AND aar=? ORDER BY total_antal DESC LIMIT 15",
            (uge, aar)
        ).fetchall()
        next_uge = uge + 1 if uge < 52 else 1
        next_aar = aar if uge < 52 else aar + 1
        best_nxt = conn.execute(
            "SELECT varenavn, total_antal FROM ugebestillinger WHERE uge=? AND aar=? ORDER BY total_antal DESC LIMIT 15",
            (next_uge, next_aar)
        ).fetchall()

    # Berig med vaekst
    kat_enriched = []
    for k in kat_nu:
        prev_oms = kat_prev.get(k['kategori'], 0)
        vaekst = round((k['oms'] - prev_oms) / prev_oms * 100, 1) if prev_oms else None
        kat_enriched.append({**dict(k), "oms_prev30": prev_oms, "vaekst_pct": vaekst})

    top_enriched = []
    for p in top_nu:
        prev_oms = top_prev_map.get(p['varenavn'], 0)
        vaekst = round((p['oms'] - prev_oms) / prev_oms * 100, 1) if prev_oms else None
        top_enriched.append({**dict(p), "oms_prev14": prev_oms, "vaekst_pct": vaekst})

    retur_pr_uge: dict = defaultdict(lambda: {'b': 0, 'w': 0})
    for r in retur_uger:
        yw = r['registreret_dato'][:7]
        if r['kategori'] == 'boller':       retur_pr_uge[yw]['b'] += r['antal']
        if r['kategori'] == 'wienerbroed':  retur_pr_uge[yw]['w'] += r['antal']

    kommende_evt = []
    for i in range(1, 4):
        u = today + timedelta(weeks=i)
        iso = u.isocalendar()
        evt = _get_event(iso[1], iso[0])
        if evt:
            from datetime import date as _d2
            mon = _d2.fromisocalendar(iso[0], iso[1], 1)
            kommende_evt.append({"uge": iso[1], "aar": iso[0], "fra": mon.isoformat(),
                                  "navn": evt["navn"], "factor": evt["factor"],
                                  "note": evt.get("note","")})

    return {
        "dato_idag":         str(today),
        "seneste_salgsdag":  seneste_dato,
        "seneste_dag":       dict(seneste_dag) if seneste_dag else {},
        "prev_dag_oms":      int(prev_dag_oms['oms'] or 0) if prev_dag_oms else None,
        "uger":              [dict(r) for r in uger_iso],
        "kategorier":        kat_enriched,
        "dag_snit":          [dict(r) for r in dag_snit],
        "top_produkter":     top_enriched,
        "tgtg_uger":         [dict(r) for r in tgtg_uger],
        "retur_pr_uge":      dict(retur_pr_uge),
        "retur_varer":       [dict(r) for r in retur_varer],
        "bestilling_nu":     [dict(r) for r in best_nu],
        "bestilling_naeste": [dict(r) for r in best_nxt],
        "kommende_events":   kommende_evt,
        "aktuel_uge":        uge,
    }

def _tabel_findes(conn, navn: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (navn,)
    ).fetchone()
    return bool(r)


def _format_management_prompt(d: dict) -> str:
    """Formaterer rig data til konkret management review prompt."""
    from datetime import date as _date
    _DAGE = ['mandag','tirsdag','onsdag','torsdag','fredag','lørdag','søndag']
    def _dn(ds):
        try: return _DAGE[_date.fromisoformat(str(ds)[:10]).weekday()]
        except: return ''

    lines = [
        "Du er erfaren detailhandels-rådgiver for Organic Market Greve — en dansk specialbutik med eget bageri.",
        "Produktmix: bagværk (boller, wienerbrød, brød fra ekstern bagerleverandør), friske råvarer, mejeriprodukter, delikatesser.",
        "KERNEFORRETNINGSLOGIK — to LIGE STORE risici:",
        "① FOR MEGET på svage dage → TGTG/retur → tab (kostpris + arbejdstid)",
        "② FOR LIDT på stærke dage → tomme hylder → tabt salg og skuffede kunder",
        "TGTG-mål: under 800 kr/uge. Over 1.200 kr = vi overbestiller på svage dage.",
        "Lørdage/fredage typisk stærke. Mandage/tirsdage typisk svage. Begivenheder kan vende mønstret.",
        "",
        "⚠ DATAKVALITET:",
        "• Shopbox er manuelt tastet — varenavn/antal kan indeholde fejl. Solgte stk er estimat, ikke præcist.",
        "• MobilePay-omsætning er ikke varekoblet — reelt bagværkssalg er højere end produktdata viser.",
        "• Brug TGTG-kr og retur-stk som primære indikatorer — de er mere pålidelige end stk-tal fra Shopbox.",
        "",
        f"DATO I DAG: {d['dato_idag']} ({_dn(d['dato_idag'])})",
        f"SENESTE SALGSDAG: {d['seneste_salgsdag']} ({_dn(d['seneste_salgsdag'])})",
        "",
    ]

    # Seneste dag med sammenligning
    sd = d.get('seneste_dag', {})
    if sd.get('oms'):
        prev = d.get('prev_dag_oms')
        diff = f" (samme dag forrige uge: {prev:,} kr, {'+' if sd['oms']>prev else ''}{round(sd['oms']-prev):,} kr)" if prev else ""
        lines += [f"SENESTE DAG ({_dn(d['seneste_salgsdag'])}): {sd['oms']:,.0f} kr · {sd.get('db_pct',0):.1f}% DB · {sd.get('kunder',0)} kunder{diff}", ""]

    # Ugentlig trend — vis udvikling
    if d.get('uger'):
        lines.append("UGENTLIG OMSAETNING OG DB (nyeste forst):")
        uger = d['uger']
        for i, u in enumerate(uger[:8]):
            change = ""
            if i+1 < len(uger) and uger[i+1].get('oms',0) > 0:
                pct = round((u['oms'] - uger[i+1]['oms']) / uger[i+1]['oms'] * 100, 1)
                change = f" ({'+' if pct>=0 else ''}{pct}% ift. forrige)"
            lines.append(f"  Uge {u['uge']}/{u['aar']} ({u.get('fra','')[:10]}–{u.get('til','')[:10]}): "
                        f"{u.get('oms',0):,.0f} kr · {u.get('db_pct',0):.1f}% DB · "
                        f"{u.get('kunder',0)} kunder · {u.get('dage',0)} dage{change}")
        lines.append("")

    # Kategorier med vaekst
    if d.get('kategorier'):
        lines.append("KATEGORIER SENESTE 30 DAGE (vs. forrige 30 dage):")
        for k in d['kategorier']:
            v = k.get('vaekst_pct')
            vaekst = f" ({'+' if v and v>=0 else ''}{v}% vaekst)" if v is not None else ""
            lines.append(f"  {k['kategori']}: {k['oms']:,.0f} kr · {k.get('db_pct',0):.1f}% DB{vaekst}")
        lines.append("")

    # Dag-af-uge
    if d.get('dag_snit'):
        lines.append("SNIT PR UGEDAG (seneste 12 uger):")
        for dag in sorted(d['dag_snit'], key=lambda x: x.get('dag_nr',0)):
            lines.append(f"  {dag['dag']}: {dag.get('snit_oms',0):,.0f} kr · {dag.get('snit_kunder',0)} kunder snit")
        lines.append("")

    # Top produkter med vaekst
    if d.get('top_produkter'):
        lines.append("TOP PRODUKTER SENESTE 14 DAGE (vs. forrige 14 dage):")
        for p in d['top_produkter'][:12]:
            v = p.get('vaekst_pct')
            vaekst = f" ({'+' if v and v>=0 else ''}{v}%)" if v is not None else " (ny)"
            lines.append(f"  {p['varenavn']}: {p['oms']:,.0f} kr · {p.get('antal',0)} stk · {p.get('db_pct',0):.1f}% DB{vaekst}")
        lines.append("")

    # TGTG analyse
    if d.get('tgtg_uger'):
        tgtg_snit = round(sum(r.get('kr',0) or 0 for r in d['tgtg_uger']) / max(len(d['tgtg_uger']),1))
        lines.append(f"TOO GOOD TO GO (TGTG) — snit {tgtg_snit:,} kr/uge (maal: under 800 kr = minimalt spild, over 1200 kr = for meget):")
        for t in d['tgtg_uger'][:6]:
            status = "OK" if (t.get('kr') or 0) < 800 else ("HOEJ" if (t.get('kr') or 0) < 1200 else "FOR HOEJ")
            lines.append(f"  {t.get('fra','')[:10]}: {t.get('poser',0)} poser · {t.get('kr',0):,.0f} kr [{status}]")
        lines.append("")

    # Retur analyse
    retur_uge_data = d.get('retur_pr_uge', {})
    if retur_uge_data:
        lines.append("RETUR BAGVAERK PR DATO (seneste 6 uger):")
        for yw, rv in sorted(retur_uge_data.items(), reverse=True)[:6]:
            lines.append(f"  {yw}: {rv['b']} boller + {rv['w']} wienerbroed returneret")
        lines.append("")
    if d.get('retur_varer'):
        lines.append("RETUR PR VARE (seneste 4 uger):")
        for r in d['retur_varer']:
            lines.append(f"  {r['produkt']} ({r['kategori']}): {r['antal']} stk returneret")
        lines.append("")

    # Bestilling
    if d.get('bestilling_nu'):
        lines.append(f"AKTUEL UGE BESTILLING (uge {d.get('aktuel_uge','?')}):")
        for b in d['bestilling_nu'][:12]:
            lines.append(f"  {b['varenavn']}: {b.get('total_antal',0)} stk")
        lines.append("")
    if d.get('bestilling_naeste'):
        lines.append(f"NAESTE UGE BESTILLING:")
        for b in d['bestilling_naeste'][:12]:
            lines.append(f"  {b['varenavn']}: {b.get('total_antal',0)} stk")
        lines.append("")

    # Kommende begivenheder
    if d.get('kommende_events'):
        lines.append("KOMMENDE BEGIVENHEDER (naeste 3 uger):")
        for e in d['kommende_events']:
            lines.append(f"  Uge {e['uge']}/{e['aar']} ({e['fra'][:10]}): {e['navn']} — faktor {e['factor']} — {e.get('note','')}")
        lines.append("")

    lines += [
        "═══════════════════════════════════════════════════════",
        "MANAGEMENT REVIEW OPGAVE:",
        "Skriv en SPECIFIK og DATADREVET management review for Organic Market Greve.",
        "Brug faktiske tal i HVERT udsagn — aldrig generiske vendinger.",
        "Sammenlign altid med forrige periode og identificer tendenser.",
        "",
        "Returner KUN valid JSON (ingen markdown, ingen forklaring udenfor JSON):",
        "",
        '{"sektioner": [',
        '  {"id": "uge_status", "titel": "Ugestatus & Omsaetning", "tone": "positiv|neutral|advarsel",',
        '   "tekst": "Specifik analyse af seneste uges performance med konkrete tal og sammenligning"},',
        '  {"id": "tgtg_spild", "titel": "TGTG & Spild", "tone": "positiv|neutral|advarsel",',
        '   "tekst": "TGTG-niveau analyseret: er vi over/under 800 kr maalet? Hvilke varer fylder poserne? Konkret anbefaling"},',
        '  {"id": "retur", "titel": "Retur & Bestillingsniveau", "tone": "positiv|neutral|advarsel",',
        '   "tekst": "Retur-analyse pr vare og pr uge. Er returraten normal (10% boller, 13.5% wiener)? Hvad skal justeres?"},',
        '  {"id": "begivenheder", "titel": "Kommende Begivenheder", "tone": "positiv|neutral|advarsel",',
        '   "tekst": "Konkrete handlinger for kommende begivenheder — hvilke dage, hvilke produkter, hvilke justeringer"},',
        '  {"id": "tiltag", "titel": "Anbefalede Handlinger", "tone": "positiv|neutral|advarsel",',
        '   "tekst": "3 konkrete, navngivne handlinger med specifikke tal: fx Reducer Croissant med 15% mandag-onsdag"}',
        ']}',
        "",
        "REGLER — overtraed ikke disse:",
        "- Hvert afsnit SKAL indeholde mindst 3 specifikke tal fra data",
        "- Skriv altid: 'X kr' ikke 'omsaetningen'",
        "- Skriv altid varenavn naar du taler om et produkt",
        "- tone 'advarsel' naar noget er over/under normalniveau og kraever handling",
        "- Brug \\n\\n til nye afsnit inden for tekst",
        "- Maks 80 ord pr sektion — vær præcis og kortfattet",
        "- Hele JSON-svaret SKAL afsluttes korrekt med ]} — afskær aldrig midt i en sætning",
    ]
    return "\n".join(lines)


def generer_management_review(api_key: str, uge: int = None, aar: int = None) -> dict:
    """Kalder Claude API og gemmer review i databasen. Returnerer det nye review.

    Note: uge og aar parametre er reserveret for fremtidig brug. I øjeblikket analyseres altid nuværende data.
    """
    import anthropic, json
    from datetime import datetime

    # Hent data for den valgte uge (eller nuværende hvis ikke specificeret)
    data = hent_management_data(uge=uge, aar=aar)
    prompt = _format_management_prompt(data)

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    # Tjek om svaret blev trunkeret
    stop_reason = msg.stop_reason
    raw = msg.content[0].text.strip()

    # Udtræk JSON (robusthed: fjern evt. markdown-wrapper)
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    # Håndter trunkeret JSON — forsøg at reparere afskåret tekst
    if stop_reason == 'max_tokens':
        # Prøv at lukke JSON manuelt hvis den er trunkeret
        if not raw.endswith('}'):
            # Afslut den åbne sektion og luk JSON
            raw = raw.rstrip(',').rstrip()
            if '"tekst"' in raw and not raw.endswith('"'):
                raw += '... [trunkeret]"}'
            raw += ']}'

    try:
        parsed = json.loads(raw)
    except Exception:
        # Fallback: byg minimal parsed struktur
        parsed = {"sektioner": [{"id": "fejl", "titel": "Trunkeret svar",
                   "tone": "advarsel",
                   "tekst": "Svaret blev afskåret. Tryk 'Generer ny' igen."}]}

    parsed["genereret"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    parsed["model"] = "claude-opus-4-5"
    if stop_reason == 'max_tokens':
        parsed["advarsel"] = "Svaret blev trunkeret — tryk Generer ny for komplet review"

    with _conn() as conn:
        conn.execute(
            "INSERT INTO management_review (data_snapshot, indhold_json) VALUES (?,?)",
            (json.dumps(data, ensure_ascii=False, default=str),
             json.dumps(parsed, ensure_ascii=False))
        )
        # Behold kun de 10 seneste
        conn.execute("""
            DELETE FROM management_review
            WHERE id NOT IN (
                SELECT id FROM management_review ORDER BY id DESC LIMIT 10
            )
        """)

    return parsed


def hent_seneste_management_review() -> dict | None:
    """Henter det seneste gemte management review."""
    import json
    with _conn() as conn:
        row = conn.execute(
            "SELECT indhold_json, genereret_dato FROM management_review ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    parsed = json.loads(row["indhold_json"])
    parsed["_db_genereret"] = row["genereret_dato"]
    return parsed


def hent_data_til_spørgsmål() -> dict:
    """Henter detaljeret data-kontekst til Q&A — mere granulat end management review."""
    from datetime import date, timedelta
    _DAGE = ['mandag','tirsdag','onsdag','torsdag','fredag','lørdag','søndag']
    today = date.today()

    with _conn() as conn:
        # Daglig omsætning seneste 60 dage
        dage_60 = conn.execute("""
            SELECT dato,
                   ROUND(SUM(omsætning),0) AS oms,
                   ROUND(SUM(avance),0)    AS db_kr,
                   SUM(antal)              AS antal
            FROM transaktioner
            WHERE dato >= date('now','-60 days')
            GROUP BY dato ORDER BY dato
        """).fetchall()

        # Ugentlig seneste 16 uger
        uger_16 = conn.execute("""
            SELECT strftime('%Y-%W', dato) AS uge_key,
                   MIN(dato) AS fra, MAX(dato) AS til,
                   ROUND(SUM(omsætning),0) AS oms,
                   ROUND(SUM(avance),0)    AS db_kr,
                   COUNT(DISTINCT dato)    AS dage
            FROM transaktioner
            WHERE dato >= date('now','-112 days')
            GROUP BY uge_key ORDER BY uge_key
        """).fetchall()

        # Top 30 produkter seneste 30 dage
        top30 = conn.execute("""
            SELECT varenavn, kategori,
                   ROUND(SUM(omsætning),0)  AS oms,
                   SUM(antal)               AS antal,
                   ROUND(AVG(omsætning/NULLIF(antal,0)),2) AS pris,
                   ROUND(SUM(avance)*100.0/NULLIF(SUM(omsætning),0),1) AS db_pct
            FROM transaktioner
            WHERE dato >= date('now','-30 days') AND varenavn != ''
            GROUP BY varenavn ORDER BY oms DESC LIMIT 30
        """).fetchall()

        # Kategorier seneste 30 dage
        kats = conn.execute("""
            SELECT kategori,
                   ROUND(SUM(omsætning),0)  AS oms,
                   ROUND(SUM(avance),0)      AS db_kr,
                   ROUND(SUM(avance)*100.0/NULLIF(SUM(omsætning),0),1) AS db_pct,
                   SUM(antal)               AS antal
            FROM transaktioner
            WHERE dato >= date('now','-30 days') AND kategori != ''
            GROUP BY kategori ORDER BY oms DESC
        """).fetchall()

        # Dag-af-uge snit seneste 12 uger
        dag_snit = conn.execute("""
            SELECT CAST(strftime('%w',dato) AS INTEGER) AS dag_nr,
                   ROUND(AVG(dag_oms),0) AS snit_oms,
                   ROUND(AVG(dag_db),0)  AS snit_db,
                   COUNT(*)              AS uger
            FROM (
                SELECT dato,
                       SUM(omsætning) AS dag_oms,
                       SUM(avance)    AS dag_db
                FROM transaktioner
                WHERE dato >= date('now','-84 days')
                GROUP BY dato
            )
            GROUP BY dag_nr ORDER BY dag_nr
        """).fetchall()

        # Månedlig seneste 12 måneder
        maaned = conn.execute("""
            SELECT strftime('%Y-%m', dato) AS mnd,
                   ROUND(SUM(omsætning),0) AS oms,
                   ROUND(SUM(avance),0)    AS db_kr
            FROM transaktioner
            WHERE dato >= date('now','-365 days')
            GROUP BY mnd ORDER BY mnd
        """).fetchall()

    dag_navne = {0:'søndag',1:'mandag',2:'tirsdag',3:'onsdag',4:'torsdag',5:'fredag',6:'lørdag'}

    return {
        "dato_idag": str(today),
        "dag_idag": _DAGE[today.weekday()],
        "dage_60": [dict(r) for r in dage_60],
        "uger_16": [dict(r) for r in uger_16],
        "top30": [dict(r) for r in top30],
        "kategorier": [dict(r) for r in kats],
        "dag_snit": [{**dict(r), "dag": dag_navne.get(r["dag_nr"], "?")} for r in dag_snit],
        "maaneder": [dict(r) for r in maaned],
    }


def besvar_data_spørgsmål(spørgsmål: str, historik: list, api_key: str) -> dict:
    """Besvarer et naturligt spørgsmål om butiksdata med tal og evt. graf."""
    import anthropic, json
    from datetime import date
    _DAGE = ['mandag','tirsdag','onsdag','torsdag','fredag','lørdag','søndag']
    today = date.today()
    dag_navn = _DAGE[today.weekday()]

    d = hent_data_til_spørgsmål()

    # Byg data-kontekst som kompakt tekst
    ctx = [
        f"DATO I DAG: {today} ({dag_navn})",
        "",
        "DAGLIG OMSÆTNING seneste 60 dage:",
    ]
    for r in d["dage_60"]:
        db_pct = round(r['db_kr']*100/r['oms'],1) if r.get('oms') and r['oms']>0 else 0
        ctx.append(f"  {r['dato']}: {r['oms']:,.0f} kr  DB {db_pct}%  {r['antal']} solgte enheder")

    ctx += ["", "UGENTLIG OMSÆTNING seneste 16 uger:"]
    for r in d["uger_16"]:
        db_pct = round(r['db_kr']*100/r['oms'],1) if r.get('oms') and r['oms']>0 else 0
        ctx.append(f"  {r['fra']} til {r['til']}: {r['oms']:,.0f} kr  DB {db_pct}%  ({r['dage']} dage)")

    ctx += ["", "MÅNEDLIG OMSÆTNING:"]
    for r in d["maaneder"]:
        db_pct = round(r['db_kr']*100/r['oms'],1) if r.get('oms') and r['oms']>0 else 0
        ctx.append(f"  {r['mnd']}: {r['oms']:,.0f} kr  DB {db_pct}%")

    ctx += ["", "DAG-AF-UGE SNIT (seneste 12 uger):"]
    for r in sorted(d["dag_snit"], key=lambda x: (x['dag_nr']+6)%7):
        ctx.append(f"  {r['dag']}: {r['snit_oms']:,.0f} kr snit  DB {r['snit_db']:,.0f} kr")

    ctx += ["", "TOP 30 PRODUKTER (seneste 30 dage):"]
    for r in d["top30"]:
        ctx.append(f"  {r['varenavn']} ({r['kategori']}): {r['oms']:,.0f} kr  {r['antal']} stk  {r['db_pct']}% DB")

    ctx += ["", "KATEGORIER (seneste 30 dage):"]
    for r in d["kategorier"]:
        ctx.append(f"  {r['kategori']}: {r['oms']:,.0f} kr  {r['db_pct']}% DB  {r['antal']} enheder")

    data_tekst = "\n".join(ctx)

    # Byg samtalehistorik
    messages = []
    for h in historik[-4:]:  # maks 4 tidligere udvekslinger
        messages.append({"role": "user",      "content": h["spørgsmål"]})
        messages.append({"role": "assistant", "content": json.dumps(h["svar"], ensure_ascii=False)})

    system = f"""Du er dataanalytiker for Organic Market Greve. Du har adgang til butikkens salgsdata.

{data_tekst}

Besvar brugerens spørgsmål på DANSK. Returner KUN valid JSON — ingen markdown, ingen tekst udenfor JSON:

{{
  "svar": "Tekst-svar med konkrete tal fra data. Brug linjeskift (\\n) til at strukturere svaret.",
  "graf": null
}}

ELLER hvis en graf er relevant:
{{
  "svar": "Tekst-svar med konkrete tal.",
  "graf": {{
    "type": "bar",
    "titel": "Grafens titel",
    "x_label": "X-akse label (valgfri)",
    "y_label": "Y-akse label (valgfri)",
    "labels": ["Label1", "Label2", ...],
    "datasets": [
      {{"label": "Serie navn", "data": [tal1, tal2, ...], "farve": "forest"}}
    ]
  }}
}}

Graf-regler:
- type: "bar", "line", "pie" eller "doughnut"
- farver: "forest" (mørkegrøn), "moss" (lysgrøn), "amber" (guld), "danger" (rød), "ash" (grå)
- Brug kun graf når det giver reel visuel værdi (trends, sammenligninger, fordelinger)
- Maks 2 datasets i samme graf
- labels: maks 16 punkter (aggreger hvis nødvendigt)
- Alle tal skal være numeriske (ikke strenge)"""

    messages.append({"role": "user", "content": spørgsmål})

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        system=system,
        messages=messages,
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)


# ── VEJR (Open-Meteo, Greve DK) ───────────────────────────────────────────────

import time as _time_mod
_vejr_cache: Dict = {"ts": 0, "data": None}
_VEJR_LAT = 55.5906
_VEJR_LON = 12.2985


def _vejr_ikon(kode: int) -> str:
    if kode == 0:              return "☀️"
    if kode in (1, 2):         return "🌤️"
    if kode == 3:              return "☁️"
    if kode in (45, 48):       return "🌫️"
    if kode in (51, 53, 55):   return "🌦️"
    if kode in (61, 63, 65):   return "🌧️"
    if kode in (71, 73, 75):   return "🌨️"
    if kode in (80, 81, 82):   return "🌦️"
    if kode in (95, 96, 99):   return "⛈️"
    return "🌡️"


def _vejr_justering(kode: int, prec: float, tmax: float) -> Dict:
    """Beregn justerings-faktor for bageriet baseret på vejr."""
    faktor = 1.0
    if prec >= 10:
        faktor -= 0.20
    elif prec >= 5:
        faktor -= 0.12
    elif prec >= 2:
        faktor -= 0.06
    if kode in (95, 96, 99):
        faktor -= 0.15
    if tmax < 2:
        faktor -= 0.05
    if prec < 1 and kode in (0, 1) and 15 <= tmax <= 25:
        faktor += 0.05
    faktor = round(max(0.6, min(1.2, faktor)), 2)
    if faktor >= 1.03:
        return {"faktor": faktor, "farve": "green",   "label": f"Godt vejr +{int((faktor-1)*100)}%"}
    if faktor <= 0.88:
        return {"faktor": faktor, "farve": "red",     "label": f"Dårligt vejr {int((faktor-1)*100)}%"}
    if faktor <= 0.95:
        return {"faktor": faktor, "farve": "orange",  "label": f"Regn {int((faktor-1)*100)}%"}
    return {"faktor": faktor, "farve": "neutral", "label": "Normalt vejr"}


def hent_varekostpris_oversigt() -> List[Dict]:
    """Alle varer med aktuel kostpris og antal historiske ændringer."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT kp.varenummer, kp.varenavn, kp.kostpris_enhed, kp.gyldig_fra, kp.kilde,
                   (SELECT COUNT(*) FROM varekostpris h
                    WHERE h.varenummer = kp.varenummer AND h.varenavn = kp.varenavn) AS antal_ændringer
            FROM varekostpris kp
            WHERE kp.gyldig_til IS NULL
            ORDER BY kp.varenavn
        """).fetchall()
    return [dict(r) for r in rows]


def hent_varekostpris_historik(varenummer: str) -> List[Dict]:
    """Fuld prishistorik for én vare."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT * FROM varekostpris WHERE varenummer=?
            ORDER BY gyldig_fra DESC
        """, (varenummer,)).fetchall()
    return [dict(r) for r in rows]


def korriger_varekostpris(varenummer: str, kostpris_enhed: float, gyldig_fra: str, varenavn: str = "") -> None:
    """Manuel korrektion — lukker aktuel post og opretter ny.
    varenavn skelner mellem varer der deler varenummer (fx single + multipak)."""
    with _conn() as conn:
        if varenavn:
            aktuel = conn.execute(
                "SELECT id, varenavn FROM varekostpris "
                "WHERE varenummer=? AND LOWER(TRIM(varenavn))=LOWER(TRIM(?)) AND gyldig_til IS NULL",
                (varenummer, varenavn)
            ).fetchone()
        else:
            aktuel = conn.execute(
                "SELECT id, varenavn FROM varekostpris WHERE varenummer=? AND gyldig_til IS NULL",
                (varenummer,)
            ).fetchone()
        navn = varenavn or (aktuel["varenavn"] if aktuel else "")
        if aktuel:
            from datetime import date as _d, timedelta as _td
            til = (_d.fromisoformat(gyldig_fra) - _td(days=1)).isoformat()
            conn.execute("UPDATE varekostpris SET gyldig_til=? WHERE id=?", (til, aktuel["id"]))
        conn.execute(
            "INSERT OR REPLACE INTO varekostpris (varenummer, varenavn, kostpris_enhed, gyldig_fra, kilde) "
            "VALUES (?,?,?,?,'manuel')",
            (varenummer, navn, round(kostpris_enhed, 4), gyldig_fra)
        )


def hent_gmail_importerede() -> set:
    with _conn() as conn:
        rows = conn.execute("SELECT msg_id FROM gmail_importerede").fetchall()
    return {r["msg_id"] for r in rows}


def gem_gmail_importeret(msg_id: str, uge: int, aar: int) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO gmail_importerede (msg_id, uge, aar) VALUES (?,?,?)",
            (msg_id, uge, aar)
        )


def log_gmail_sync(status: str, besked: str, antal: int = 0) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO gmail_sync_log (status, besked, antal) VALUES (?,?,?)",
            (status, besked, antal)
        )


def hent_gmail_sync_status() -> Optional[Dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM gmail_sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def hent_sidst_solgt_moenster(uger: int = 4) -> Dict:
    """Beregn gennemsnitlig sidst-solgt tid per vare over de seneste N uger.
    Butik åbner 06:00, lukker 20:00.
    < 14:00 = sandsynligvis udsolgt (tomme hylder)
    > 18:00 = overskud ved lukketid
    """
    from datetime import date as _d, timedelta as _td
    fra = (_d.today() - _td(weeks=uger)).isoformat()
    _KAGE_EXCL = (
        "LOWER(t.varenavn) LIKE '%kage%' OR LOWER(t.varenavn) LIKE '%cookie%' "
        "OR LOWER(t.varenavn) LIKE '%muffin%' OR LOWER(t.varenavn) LIKE '%brownie%' "
        "OR LOWER(t.varenavn) LIKE '%romkugl%' OR LOWER(t.varenavn) LIKE '%napoleon%' "
        "OR LOWER(t.varenavn) LIKE '%studenterbr%'"
    )
    with _conn() as conn:
        rows = conn.execute(f"""
            SELECT t.varenavn,
                   ROUND(AVG(sidst_time), 0) AS snit_sidst_time,
                   COUNT(DISTINCT dato)       AS dage_med_salg,
                   MIN(sidst_time)            AS min_tid,
                   MAX(sidst_time)            AS max_tid
            FROM (
                SELECT t.dato, t.varenavn, MAX(t.time_start) AS sidst_time
                FROM transaktioner t
                WHERE t.dato >= ?
                  AND t.time_start BETWEEN 6 AND 19
                  AND t.antal > 0
                  -- Kun bagværk fra ugebestillinger (ekskl. kager)
                  AND CAST(CAST(t.varenummer AS REAL) AS INTEGER) IN (
                      SELECT DISTINCT CAST(CAST(varenummer AS REAL) AS INTEGER)
                      FROM ugebestillinger
                      WHERE varenummer != '' AND varenummer != '0'
                        AND NOT ({_KAGE_EXCL.replace('t.varenavn','varenavn')})
                  )
                GROUP BY t.dato, t.varenavn
            ) t
            GROUP BY t.varenavn
            HAVING dage_med_salg >= 3
            ORDER BY snit_sidst_time ASC
        """, (fra,)).fetchall()

    udsolgt = []
    overskud = []
    for r in rows:
        t = int(r['snit_sidst_time'] or 0)
        item = {
            "varenavn":     r['varenavn'],
            "snit_time":    t,
            "dage":         int(r['dage_med_salg']),
            "tomme_timer":  20 - t,
        }
        if t < 14:
            udsolgt.append(item)
        elif t > 18:
            overskud.append(item)

    return {
        "udsolgt_tidligt": sorted(udsolgt,  key=lambda x: x['snit_time']),
        "overskud_sent":   sorted(overskud, key=lambda x: -x['snit_time']),
        "periode_uger":    uger,
    }


def hent_vejr_forecast() -> Dict:
    """Hent 14-dages vejrudsigt for Greve fra Open-Meteo. Cache 3 timer."""
    import urllib.request as _urlreq
    import json as _json2
    from datetime import datetime as _dt

    now = _time_mod.time()
    if _vejr_cache["data"] and (now - _vejr_cache["ts"]) < 10800:
        return _vejr_cache["data"]

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={_VEJR_LAT}&longitude={_VEJR_LON}"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode"
        f"&timezone=Europe%2FCopenhagen"
        f"&forecast_days=14"
    )
    try:
        with _urlreq.urlopen(url, timeout=6) as resp:
            raw = _json2.loads(resp.read())
        daily  = raw.get("daily", {})
        dates  = daily.get("time", [])
        tmax   = daily.get("temperature_2m_max", [])
        tmin   = daily.get("temperature_2m_min", [])
        prec   = daily.get("precipitation_sum", [])
        codes  = daily.get("weathercode", [])

        forecast = {}
        for i, dato in enumerate(dates):
            tx = round(tmax[i] or 0, 1) if i < len(tmax) else None
            pr = round(prec[i] or 0, 1) if i < len(prec) else 0.0
            kd = int(codes[i]) if i < len(codes) else 0
            forecast[dato] = {
                "dato":   dato,
                "tmax":   tx,
                "tmin":   round(tmin[i] or 0, 1) if i < len(tmin) else None,
                "prec":   pr,
                "kode":   kd,
                "ikon":   _vejr_ikon(kd),
                "juster": _vejr_justering(kd, pr, tx or 15),
            }
        result = {
            "opdateret": _dt.now().isoformat(timespec="minutes"),
            "forecast":  forecast,
        }
    except Exception as e:
        result = {"opdateret": None, "forecast": {}, "fejl": str(e)}

    _vejr_cache["ts"]   = now
    _vejr_cache["data"] = result
    return result
