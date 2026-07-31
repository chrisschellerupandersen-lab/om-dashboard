import sqlite3
import os
import json
import hashlib
import calendar
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional

from rapidfuzz import fuzz

DB_PATH = os.environ.get("DB_PATH", "regnskab.db")


def _conn() -> sqlite3.Connection:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with _conn() as conn:
        # GoCardless Bank Account Data lukkede for nye tilmeldinger i juli 2025 — erstattet
        # af Enable Banking, som ikke bruger et server-cachet token (JWT signeres pr. kald).
        conn.execute("DROP TABLE IF EXISTS gocardless_token")

        # Migration: bank_forbindelser skal understøtte flere konti pr. requisition
        # (én GoCardless-requisition kan pege på flere konti hos samme bank) — derfor
        # er kontoen, ikke requisitionen, den unikke nøgle.
        # OBS: bygget som "opret under nyt navn, drop det gamle, omdøb til det rigtige
        # navn" — IKKE "omdøb det gamle, opret nyt, drop det gamle". SQLite omskriver
        # automatisk andre tabellers REFERENCES-klausuler ved ALTER TABLE RENAME, så den
        # oprindelige rækkefølge fik banktransaktioner.bank_forbindelse_id til varigt at
        # pege på det midlertidige (senere droppede) tabelnavn — se rettelsen nedenfor.
        _bf = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='bank_forbindelser'"
        ).fetchone()
        if _bf and "account_id" not in (_bf["sql"] or ""):
            conn.executescript("""
                CREATE TABLE bank_forbindelser_ny (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    institution_id     TEXT,
                    institution_navn   TEXT,
                    account_id         TEXT UNIQUE,
                    iban               TEXT,
                    requisition_id     TEXT,
                    consent_expires_ts TEXT,
                    status             TEXT DEFAULT 'aktiv'
                );
                INSERT INTO bank_forbindelser_ny (id, institution_id, iban, requisition_id, consent_expires_ts, status)
                    SELECT id, institution_id, iban, requisition_id, consent_expires_ts, status FROM bank_forbindelser;
                DROP TABLE bank_forbindelser;
                ALTER TABLE bank_forbindelser_ny RENAME TO bank_forbindelser;
            """)

        # Migration: saldo-visning pr. bankforbindelse (kun ADD COLUMN — ingen RENAME,
        # så banktransaktioner.bank_forbindelse_id-referencen påvirkes ikke). Tabellen
        # findes ikke endnu ved en helt frisk database — CREATE TABLE IF NOT EXISTS
        # nedenfor opretter den allerede med saldo-kolonnerne i så fald.
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bank_forbindelser'"
        ).fetchone():
            _bf_kolonner = {r["name"] for r in conn.execute("PRAGMA table_info(bank_forbindelser)").fetchall()}
            if "saldo" not in _bf_kolonner:
                conn.execute("ALTER TABLE bank_forbindelser ADD COLUMN saldo REAL")
            if "saldo_ts" not in _bf_kolonner:
                conn.execute("ALTER TABLE bank_forbindelser ADD COLUMN saldo_ts TEXT")

        # Rettelse: banktransaktioner.bank_forbindelse_id fik (via ovenstående migrations
        # oprindelige, forkerte rækkefølge) en varig REFERENCES-klausul til det droppede
        # midlertidige tabelnavn "bank_forbindelser_gl" — genopbyg tabellen så referencen
        # peger korrekt på "bank_forbindelser" igen. Rammer kun databaser der allerede har
        # kørt den gamle migration; ny-installationer ser aldrig dette.
        _bt = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='banktransaktioner'"
        ).fetchone()
        if _bt and "bank_forbindelser_gl" in (_bt["sql"] or ""):
            conn.executescript("""
                ALTER TABLE banktransaktioner RENAME TO banktransaktioner_gl;
                CREATE TABLE banktransaktioner (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    bank_forbindelse_id INTEGER REFERENCES bank_forbindelser(id),
                    ekstern_id          TEXT UNIQUE,
                    dato                TEXT NOT NULL,
                    beloeb              REAL NOT NULL,
                    tekst               TEXT,
                    match_status        TEXT DEFAULT 'uafklaret',
                    matchet_type        TEXT,
                    matchet_id          INTEGER,
                    importeret_ts       TEXT DEFAULT (datetime('now','localtime'))
                );
                INSERT INTO banktransaktioner SELECT * FROM banktransaktioner_gl;
                DROP TABLE banktransaktioner_gl;
            """)

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS kontoplan (
                kontonr   TEXT PRIMARY KEY,
                navn      TEXT NOT NULL,
                kontotype TEXT NOT NULL,   -- omsaetning|omkostning|aktiv|passiv|moms|status
                moms_kode TEXT,
                aktiv     INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS bilag (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                type             TEXT NOT NULL DEFAULT 'leverandoerfaktura',
                fil_sti          TEXT NOT NULL,
                fil_sha256       TEXT NOT NULL UNIQUE,
                kilde            TEXT DEFAULT 'upload',
                ai_raw_json      TEXT,
                ai_model         TEXT,
                leverandoer_cvr  TEXT,
                leverandoer_navn TEXT,
                fakturanr        TEXT,
                fakturadato      TEXT,
                forfaldsdato     TEXT,
                beloeb_ex_moms   REAL DEFAULT 0,
                moms_beloeb      REAL DEFAULT 0,
                beloeb_total     REAL DEFAULT 0,
                valuta           TEXT DEFAULT 'DKK',
                kontonr          TEXT REFERENCES kontoplan(kontonr),
                status           TEXT NOT NULL DEFAULT 'afventer', -- afventer|godkendt|afvist|bogfoert
                kreditor_id      INTEGER REFERENCES kreditorer(id),
                godkendt_af      TEXT,
                godkendt_ts      TEXT,
                oprettet_ts      TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS bilag_linjer (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                bilag_id    INTEGER NOT NULL REFERENCES bilag(id),
                beskrivelse TEXT,
                beloeb      REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS posteringer (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                dato          TEXT NOT NULL,
                tekst         TEXT NOT NULL,
                bilag_id      INTEGER REFERENCES bilag(id),
                bruger        TEXT NOT NULL,
                korrigerer_id INTEGER REFERENCES posteringer(id),
                hash_prev     TEXT,
                hash_self     TEXT NOT NULL,
                oprettet_ts   TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS posteringslinjer (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                postering_id INTEGER NOT NULL REFERENCES posteringer(id),
                kontonr      TEXT NOT NULL REFERENCES kontoplan(kontonr),
                debet        REAL NOT NULL DEFAULT 0,
                kredit       REAL NOT NULL DEFAULT 0,
                modpart_type TEXT,   -- debitor|kreditor
                modpart_id   INTEGER
            );

            CREATE TABLE IF NOT EXISTS debitorer (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                navn   TEXT NOT NULL,
                cvr    TEXT,
                adresse TEXT,
                email  TEXT,
                betalingsvilkaar_dage INTEGER DEFAULT 8
            );

            CREATE TABLE IF NOT EXISTS debitor_poster (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                debitor_id   INTEGER NOT NULL REFERENCES debitorer(id),
                fakturanr    TEXT,
                dato         TEXT,
                forfaldsdato TEXT,
                beloeb       REAL NOT NULL,
                restbeloeb   REAL NOT NULL,
                status       TEXT NOT NULL DEFAULT 'aaben', -- aaben|delvist|udlignet
                postering_id INTEGER REFERENCES posteringer(id)
            );

            CREATE TABLE IF NOT EXISTS kreditorer (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                navn     TEXT NOT NULL,
                cvr      TEXT,
                adresse  TEXT,
                email    TEXT,
                standard_kontonr TEXT REFERENCES kontoplan(kontonr),
                betalingsvilkaar_dage INTEGER DEFAULT 8,
                iban TEXT,
                bic  TEXT
            );

            CREATE TABLE IF NOT EXISTS konteringsregler (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                kreditor_id INTEGER NOT NULL REFERENCES kreditorer(id),
                match_tekst TEXT NOT NULL,   -- nøgleord der skal findes i fakturaindholdet
                kontonr     TEXT NOT NULL REFERENCES kontoplan(kontonr),
                beskrivelse TEXT,
                oprettet_ts TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS kreditor_poster (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                kreditor_id  INTEGER NOT NULL REFERENCES kreditorer(id),
                bilag_id     INTEGER REFERENCES bilag(id),
                fakturanr    TEXT,
                dato         TEXT,
                forfaldsdato TEXT,
                beloeb       REAL NOT NULL,
                restbeloeb   REAL NOT NULL,
                status       TEXT NOT NULL DEFAULT 'aaben', -- aaben|udvalgt|betalt-afventer-match|udlignet
                postering_id INTEGER REFERENCES posteringer(id)
            );

            CREATE TABLE IF NOT EXISTS bank_forbindelser (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                institution_id     TEXT,
                institution_navn   TEXT,
                account_id         TEXT UNIQUE,
                iban               TEXT,
                requisition_id     TEXT,
                consent_expires_ts TEXT,
                status             TEXT DEFAULT 'aktiv',   -- aktiv|udloebet|tilbagekaldt
                saldo              REAL,
                saldo_ts           TEXT
            );

            CREATE TABLE IF NOT EXISTS banktransaktioner (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                bank_forbindelse_id INTEGER REFERENCES bank_forbindelser(id),
                ekstern_id          TEXT UNIQUE,
                dato                TEXT NOT NULL,
                beloeb              REAL NOT NULL,
                tekst               TEXT,
                match_status        TEXT DEFAULT 'uafklaret', -- uafklaret|foreslaaet|godkendt
                matchet_type        TEXT,
                matchet_id          INTEGER,
                importeret_ts       TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS betalingsbatch (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                oprettet_ts    TEXT DEFAULT (datetime('now','localtime')),
                oprettet_af    TEXT,
                status         TEXT DEFAULT 'kladde', -- kladde|eksporteret|sendt|afstemt
                execution_date TEXT,
                xml_fil_sti    TEXT
            );

            CREATE TABLE IF NOT EXISTS betalingsbatch_linjer (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id        INTEGER NOT NULL REFERENCES betalingsbatch(id),
                kreditor_post_id INTEGER NOT NULL REFERENCES kreditor_poster(id),
                beloeb          REAL NOT NULL,
                end_to_end_id   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT DEFAULT (datetime('now','localtime')),
                bruger        TEXT,
                handling      TEXT,
                entitet       TEXT,
                entitet_id    INTEGER,
                detaljer_json TEXT
            );
        """)

        # Append-only: posteringer og posteringslinjer må aldrig ændres/slettes
        # efter bogføring — det er selve pointen med et revisionsspor.
        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS forbyd_update_posteringer
            BEFORE UPDATE ON posteringer
            BEGIN SELECT RAISE(ABORT, 'Posteringer er append-only — brug en korrektionspostering'); END;

            CREATE TRIGGER IF NOT EXISTS forbyd_delete_posteringer
            BEFORE DELETE ON posteringer
            BEGIN SELECT RAISE(ABORT, 'Posteringer maa ikke slettes'); END;

            CREATE TRIGGER IF NOT EXISTS forbyd_update_posteringslinjer
            BEFORE UPDATE ON posteringslinjer
            BEGIN SELECT RAISE(ABORT, 'Posteringslinjer er append-only'); END;

            CREATE TRIGGER IF NOT EXISTS forbyd_delete_posteringslinjer
            BEFORE DELETE ON posteringslinjer
            BEGIN SELECT RAISE(ABORT, 'Posteringslinjer maa ikke slettes'); END;
        """)

        _seed_kontoplan(conn)


def _seed_kontoplan(conn: sqlite3.Connection):
    konti = [
        # ── Omsætning ──
        ("1000", "Varesalg",                        "omsaetning", "salgsmoms"),
        ("1100", "Andet salg og serviceydelser",     "omsaetning", "salgsmoms"),
        # ── Vareforbrug ──
        ("2000", "Vareforbrug",                      "omkostning", "koebsmoms"),
        ("2100", "Fragt og distribution ved varekøb", "omkostning", "koebsmoms"),
        # ── Lokaleomkostninger ──
        ("3000", "Husleje",                           "omkostning", "momsfri"),
        ("3010", "El, vand og varme",                 "omkostning", "koebsmoms"),
        ("3020", "Rengøring",                         "omkostning", "koebsmoms"),
        ("3030", "Ejendomsforsikring",                "omkostning", "momsfri"),
        # ── Salg og marketing ──
        ("3100", "Reklame og marketing",              "omkostning", "koebsmoms"),
        ("3110", "Repræsentation",                    "omkostning", "reduceret"),
        ("3150", "Autodrift og transport",            "omkostning", "koebsmoms"),
        # ── Administration ──
        ("3200", "Kontorartikler og tryksager",       "omkostning", "koebsmoms"),
        ("3210", "Telefon og internet",                "omkostning", "koebsmoms"),
        ("3220", "IT og softwareabonnementer",         "omkostning", "koebsmoms"),
        ("3230", "Porto og gebyrer",                   "omkostning", "momsfri"),
        ("3240", "Revisor og bogholderi",              "omkostning", "koebsmoms"),
        ("3250", "Forsikringer, erhverv",              "omkostning", "momsfri"),
        # ── Personaleomkostninger ──
        ("3400", "Gager og lønninger",                 "omkostning", None),
        ("3410", "ATP og pension",                     "omkostning", None),
        ("3420", "Personaleforsikringer",              "omkostning", "momsfri"),
        ("3430", "Kurser og personalegoder",           "omkostning", "koebsmoms"),
        # ── Diverse / afskrivninger ──
        ("4000", "Diverse driftsomkostninger",         "omkostning", "koebsmoms"),
        ("4100", "Afskrivninger, inventar og maskiner", "omkostning", None),
        # ── Finansielle poster ──
        ("5000", "Renteindtægter",                     "omsaetning", "momsfri"),
        ("5100", "Renteomkostninger",                   "omkostning", None),
        ("5200", "Gebyrer, finansielle",                "omkostning", "momsfri"),
        # ── Balance: aktiver ──
        ("6700", "Kasse",                               "aktiv",  None),
        ("6750", "Bank",                                "aktiv",  None),
        ("6900", "Debitorer (tilgodehavender)",         "aktiv",  None),
        ("6910", "Andre tilgodehavender",               "aktiv",  None),
        # ── Balance: passiver ──
        ("6800", "Kreditorer (leverandørgæld)",         "passiv", None),
        ("6970", "Skyldig A-skat og AM-bidrag",         "passiv", None),
        ("6980", "Skyldig løn og feriepenge",           "passiv", None),
        ("6990", "Anden gæld",                          "passiv", None),
        # ── Moms ──
        ("6960", "Købsmoms",                            "moms",   None),
        ("6961", "Salgsmoms",                           "moms",   None),
        # ── Egenkapital ──
        ("9999", "Egenkapital / åbningsbalance",        "status", None),
    ]
    for kontonr, navn, kontotype, moms_kode in konti:
        conn.execute(
            "INSERT OR IGNORE INTO kontoplan (kontonr, navn, kontotype, moms_kode) VALUES (?, ?, ?, ?)",
            (kontonr, navn, kontotype, moms_kode),
        )

    # Engangsrettelse af tidligere ASCII-erstatninger for æøå (ramte kun disse tre konti).
    # Matcher kun den PRÆCISE gamle tekst, så brugerens egne rettelser via kontoplan-siden
    # aldrig bliver overskrevet igen ved næste opstart.
    for kontonr, gammel, ny in [
        ("6960", "Koebsmoms", "Købsmoms"),
        ("6800", "Kreditorer (leverandoergaeld)", "Kreditorer (leverandørgæld)"),
        ("9999", "Egenkapital / aabningsbalance", "Egenkapital / åbningsbalance"),
    ]:
        conn.execute(
            "UPDATE kontoplan SET navn = ? WHERE kontonr = ? AND navn = ?",
            (ny, kontonr, gammel),
        )


# ── Posteringer (dobbelt bogholderi) ────────────────────────────────────────

def bogfoer_postering(dato: str, tekst: str, bruger: str, linjer: List[Dict[str, Any]],
                       bilag_id: Optional[int] = None, korrigerer_id: Optional[int] = None) -> int:
    """linjer: [{"kontonr": "...", "debet": 0, "kredit": 0, "modpart_type": None, "modpart_id": None}, ...]
    Kaster ValueError hvis linjerne ikke balancerer (sum debet != sum kredit)."""
    sum_debet = round(sum(l.get("debet", 0) or 0 for l in linjer), 2)
    sum_kredit = round(sum(l.get("kredit", 0) or 0 for l in linjer), 2)
    if sum_debet != sum_kredit:
        raise ValueError(f"Postering balancerer ikke: debet {sum_debet} != kredit {sum_kredit}")
    if sum_debet == 0:
        raise ValueError("Postering har ingen beløb")

    with _conn() as conn:
        prev = conn.execute("SELECT hash_self FROM posteringer ORDER BY id DESC LIMIT 1").fetchone()
        hash_prev = prev["hash_self"] if prev else ""
        payload = json.dumps({
            "hash_prev": hash_prev, "dato": dato, "tekst": tekst, "bruger": bruger,
            "linjer": linjer, "ts": datetime.now().isoformat(),
        }, sort_keys=True, default=str)
        hash_self = hashlib.sha256(payload.encode()).hexdigest()

        cur = conn.execute(
            "INSERT INTO posteringer (dato, tekst, bilag_id, bruger, korrigerer_id, hash_prev, hash_self) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (dato, tekst, bilag_id, bruger, korrigerer_id, hash_prev, hash_self),
        )
        postering_id = cur.lastrowid
        for l in linjer:
            conn.execute(
                "INSERT INTO posteringslinjer (postering_id, kontonr, debet, kredit, modpart_type, modpart_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (postering_id, l["kontonr"], l.get("debet", 0) or 0, l.get("kredit", 0) or 0,
                 l.get("modpart_type"), l.get("modpart_id")),
            )
        _log(conn, bruger, "bogfoer_postering", "postering", postering_id, {"tekst": tekst, "linjer": linjer})
        return postering_id


def _log(conn: sqlite3.Connection, bruger: str, handling: str, entitet: str,
         entitet_id: Optional[int], detaljer: Dict[str, Any]):
    conn.execute(
        "INSERT INTO audit_log (bruger, handling, entitet, entitet_id, detaljer_json) VALUES (?, ?, ?, ?, ?)",
        (bruger, handling, entitet, entitet_id, json.dumps(detaljer, default=str)),
    )


def nulstil_testdata():
    """Rydder alt regnskabsdata (bilag, posteringer, debitor/kreditor, konteringsregler,
    betalingsbatch, audit-log) — til brug mens systemet stadig er under test. Rører IKKE
    bank_forbindelser eller banktransaktioner (bankhistorikken skal bevares), og heller
    ikke kontoplan (kontoopsætningen er struktur, ikke testdata).

    Posteringer/posteringslinjer er ellers append-only (håndhævet af triggers, som blokerer
    almindelig DELETE) — her omgås det bevidst via DROP+CREATE, som ikke udløser
    rækkebaserede triggers. Triggerne genskabes umiddelbart efter."""
    with _conn() as conn:
        # debitor_poster/kreditor_poster har postering_id-FK'er ind i posteringer — med
        # PRAGMA foreign_keys=ON udfører SQLite en implicit DELETE FROM posteringer, FØR den
        # dropper tabellen, for at sikre fremmednøgle-integritet. Den implicitte DELETE
        # rammer selvfølgelig vores egen forbyd_delete_posteringer-trigger og fejler. Derfor
        # skal alt der peger på posteringer(id) ryddes FØR posteringer/posteringslinjer droppes.
        for tabel in ["betalingsbatch_linjer", "debitor_poster", "kreditor_poster"]:
            conn.execute(f"DELETE FROM {tabel}")
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (tabel,))

        conn.executescript("""
            DROP TABLE IF EXISTS posteringslinjer;
            DROP TABLE IF EXISTS posteringer;

            CREATE TABLE posteringer (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                dato          TEXT NOT NULL,
                tekst         TEXT NOT NULL,
                bilag_id      INTEGER REFERENCES bilag(id),
                bruger        TEXT NOT NULL,
                korrigerer_id INTEGER REFERENCES posteringer(id),
                hash_prev     TEXT,
                hash_self     TEXT NOT NULL,
                oprettet_ts   TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE posteringslinjer (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                postering_id INTEGER NOT NULL REFERENCES posteringer(id),
                kontonr      TEXT NOT NULL REFERENCES kontoplan(kontonr),
                debet        REAL NOT NULL DEFAULT 0,
                kredit       REAL NOT NULL DEFAULT 0,
                modpart_type TEXT,
                modpart_id   INTEGER
            );

            CREATE TRIGGER forbyd_update_posteringer
            BEFORE UPDATE ON posteringer
            BEGIN SELECT RAISE(ABORT, 'Posteringer er append-only — brug en korrektionspostering'); END;

            CREATE TRIGGER forbyd_delete_posteringer
            BEFORE DELETE ON posteringer
            BEGIN SELECT RAISE(ABORT, 'Posteringer maa ikke slettes'); END;

            CREATE TRIGGER forbyd_update_posteringslinjer
            BEFORE UPDATE ON posteringslinjer
            BEGIN SELECT RAISE(ABORT, 'Posteringslinjer er append-only'); END;

            CREATE TRIGGER forbyd_delete_posteringslinjer
            BEFORE DELETE ON posteringslinjer
            BEGIN SELECT RAISE(ABORT, 'Posteringslinjer maa ikke slettes'); END;
        """)

        # Resten: børn før forældre, så FK-håndhævelsen ikke fejler.
        for tabel in [
            "betalingsbatch", "konteringsregler", "bilag_linjer", "bilag",
            "debitorer", "kreditorer", "audit_log",
        ]:
            conn.execute(f"DELETE FROM {tabel}")
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (tabel,))
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('posteringer', 'posteringslinjer')")


def hent_posteringer(limit: int = 200, aar: Optional[int] = None, maaned: Optional[int] = None) -> List[Dict[str, Any]]:
    with _conn() as conn:
        params: List[Any] = []
        where = ""
        if aar is not None:
            where = "WHERE dato LIKE ?"
            params.append(f"{aar:04d}-{maaned:02d}-%" if maaned else f"{aar:04d}-%")
        params.append(limit)
        rows = conn.execute(
            f"SELECT * FROM posteringer {where} ORDER BY id DESC LIMIT ?", params
        ).fetchall()
        out = []
        for r in rows:
            linjer = conn.execute(
                "SELECT * FROM posteringslinjer WHERE postering_id = ? ORDER BY id", (r["id"],)
            ).fetchall()
            out.append({**dict(r), "linjer": [dict(l) for l in linjer]})
        return out


def hent_kontoplan(kun_aktive: bool = True) -> List[Dict[str, Any]]:
    with _conn() as conn:
        if kun_aktive:
            rows = conn.execute("SELECT * FROM kontoplan WHERE aktiv = 1 ORDER BY kontonr").fetchall()
        else:
            rows = conn.execute("SELECT * FROM kontoplan ORDER BY kontonr").fetchall()
        return [dict(r) for r in rows]


def hent_en_konto(kontonr: str) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM kontoplan WHERE kontonr = ?", (kontonr,)).fetchone()
        return dict(r) if r else None


def opret_konto(kontonr: str, navn: str, kontotype: str, moms_kode: Optional[str], bruger: str) -> None:
    with _conn() as conn:
        try:
            conn.execute(
                "INSERT INTO kontoplan (kontonr, navn, kontotype, moms_kode, aktiv) VALUES (?, ?, ?, ?, 1)",
                (kontonr, navn, kontotype, moms_kode),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Kontonummer {kontonr} findes allerede")
        _log(conn, bruger, "opret_konto", "kontoplan", None, {"kontonr": kontonr, "navn": navn})


def opdater_konto(kontonr: str, navn: str, kontotype: str, moms_kode: Optional[str],
                   aktiv: bool, bruger: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE kontoplan SET navn = ?, kontotype = ?, moms_kode = ?, aktiv = ? WHERE kontonr = ?",
            (navn, kontotype, moms_kode, 1 if aktiv else 0, kontonr),
        )
        _log(conn, bruger, "opdater_konto", "kontoplan", None, {"kontonr": kontonr, "navn": navn})


# ── Bilag ────────────────────────────────────────────────────────────────

def opret_bilag(fil_sti: str, fil_sha256: str, ai_raw_json: Optional[str] = None,
                 ai_model: Optional[str] = None, felter: Optional[Dict[str, Any]] = None,
                 kilde: str = "upload") -> Optional[int]:
    felter = felter or {}
    with _conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO bilag (type, fil_sti, fil_sha256, ai_raw_json, ai_model, kilde, "
                "leverandoer_cvr, leverandoer_navn, fakturanr, fakturadato, forfaldsdato, "
                "beloeb_ex_moms, moms_beloeb, beloeb_total, kontonr) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    felter.get("type", "leverandoerfaktura"), fil_sti, fil_sha256, ai_raw_json, ai_model, kilde,
                    felter.get("leverandoer_cvr"), felter.get("leverandoer_navn"),
                    felter.get("fakturanr"), felter.get("fakturadato"), felter.get("forfaldsdato"),
                    felter.get("beloeb_ex_moms", 0), felter.get("moms_beloeb", 0),
                    felter.get("beloeb_total", 0), felter.get("kontonr"),
                ),
            )
            _log(conn, felter.get("bruger", "system"), "opret_bilag", "bilag", cur.lastrowid, felter)
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # samme fil (sha256) allerede uploadet


def hent_bilag(status: Optional[str] = None) -> List[Dict[str, Any]]:
    with _conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM bilag WHERE status = ? ORDER BY id DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM bilag ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def hent_et_bilag(bilag_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM bilag WHERE id = ?", (bilag_id,)).fetchone()
        return dict(r) if r else None


def afvis_bilag(bilag_id: int, bruger: str):
    with _conn() as conn:
        conn.execute("UPDATE bilag SET status = 'afvist' WHERE id = ? AND status = 'afventer'", (bilag_id,))
        _log(conn, bruger, "afvis_bilag", "bilag", bilag_id, {})


def hent_kreditor_post_for_bilag(bilag_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM kreditor_poster WHERE bilag_id = ?", (bilag_id,)).fetchone()
        return dict(r) if r else None


def hent_banktransaktion_for_match(modpart_type: str, post_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        r = conn.execute(
            "SELECT * FROM banktransaktioner WHERE matchet_type = ? AND matchet_id = ?",
            (modpart_type, post_id),
        ).fetchone()
        return dict(r) if r else None


def _hent_postering_med_linjer(postering_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        p = conn.execute("SELECT * FROM posteringer WHERE id = ?", (postering_id,)).fetchone()
        if not p:
            return None
        linjer = conn.execute(
            "SELECT * FROM posteringslinjer WHERE postering_id = ?", (postering_id,)
        ).fetchall()
        return {**dict(p), "linjer": [dict(l) for l in linjer]}


def slet_bilag(bilag_id: int, bruger: str) -> Dict[str, Any]:
    """Fjern et fejlagtigt uploadet bilag.
    - afventer/afvist (intet bogført endnu): hård sletning af bilag-rækken.
    - bogfoert og IKKE bankmatchet: annulleres via en korrektionspostering der modsvarer
      den oprindelige postering 1:1, kreditorposten lukkes, bilaget markeres 'slettet'
      (selve bilags-rækken og filen bevares — bogføringssporet må ikke forsvinde).
    - bogfoert og bankmatchet: nægtes.
    Returnerer {"fil_sti": <str eller None>} — main.py sletter filen fra disk kun når den er sat
    (dvs. når intet bogføringsspor skal bevare den)."""
    bilag = hent_et_bilag(bilag_id)
    if not bilag:
        raise ValueError("Bilag findes ikke")

    if bilag["status"] in ("afventer", "afvist"):
        with _conn() as conn:
            conn.execute("DELETE FROM bilag_linjer WHERE bilag_id = ?", (bilag_id,))
            conn.execute("DELETE FROM bilag WHERE id = ?", (bilag_id,))
            _log(conn, bruger, "slet_bilag", "bilag", bilag_id, {"status_foer": bilag["status"]})
        return {"fil_sti": bilag["fil_sti"]}

    if bilag["status"] != "bogfoert":
        raise ValueError(f"Bilag har status '{bilag['status']}' — kan ikke slettes")

    kp = hent_kreditor_post_for_bilag(bilag_id)
    if kp and kp["status"] == "udlignet":
        raise ValueError("Bilaget er matchet mod en banktransaktion og kan ikke slettes — fjern først bankmatchet.")

    with _conn() as conn:
        original = conn.execute(
            "SELECT id FROM posteringer WHERE bilag_id = ? ORDER BY id ASC LIMIT 1", (bilag_id,)
        ).fetchone()

    if original:
        postering = _hent_postering_med_linjer(original["id"])
        modsat_linjer = [
            {"kontonr": l["kontonr"], "debet": l["kredit"], "kredit": l["debet"],
             "modpart_type": l["modpart_type"], "modpart_id": l["modpart_id"]}
            for l in postering["linjer"]
        ]
        bogfoer_postering(
            date.today().isoformat(),
            f"Annullering af bilag #{bilag_id} — {bilag.get('leverandoer_navn') or ''}".strip(" —"),
            bruger, modsat_linjer, bilag_id=bilag_id, korrigerer_id=postering["id"],
        )

    with _conn() as conn:
        if kp:
            conn.execute("UPDATE kreditor_poster SET status = 'annulleret', restbeloeb = 0 WHERE id = ?", (kp["id"],))
        conn.execute("UPDATE bilag SET status = 'slettet' WHERE id = ?", (bilag_id,))
        _log(conn, bruger, "annuller_bilag", "bilag", bilag_id, {})
    return {"fil_sti": None}


def find_kreditor_by_cvr(cvr: str) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM kreditorer WHERE cvr = ?", (cvr,)).fetchone()
        return dict(r) if r else None


def find_or_create_kreditor(navn: str, cvr: Optional[str] = None, adresse: Optional[str] = None) -> int:
    with _conn() as conn:
        if cvr:
            r = conn.execute("SELECT id FROM kreditorer WHERE cvr = ?", (cvr,)).fetchone()
            if r:
                return r["id"]
        r = conn.execute("SELECT id FROM kreditorer WHERE navn = ?", (navn,)).fetchone()
        if r:
            return r["id"]
        cur = conn.execute(
            "INSERT INTO kreditorer (navn, cvr, adresse) VALUES (?, ?, ?)", (navn, cvr, adresse)
        )
        return cur.lastrowid


def opret_kreditor(navn: str, cvr: Optional[str] = None, adresse: Optional[str] = None,
                    email: Optional[str] = None, iban: Optional[str] = None, bic: Optional[str] = None,
                    standard_kontonr: Optional[str] = None) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO kreditorer (navn, cvr, adresse, email, iban, bic, standard_kontonr) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (navn, cvr, adresse, email, iban, bic, standard_kontonr),
        )
        return cur.lastrowid


def hent_kreditorer() -> List[Dict[str, Any]]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM kreditorer ORDER BY navn").fetchall()]


def hent_en_kreditor(kreditor_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM kreditorer WHERE id = ?", (kreditor_id,)).fetchone()
        return dict(r) if r else None


def opdater_kreditor(kreditor_id: int, bruger: str, navn: str, cvr: Optional[str] = None,
                      adresse: Optional[str] = None, email: Optional[str] = None,
                      iban: Optional[str] = None, bic: Optional[str] = None,
                      standard_kontonr: Optional[str] = None):
    with _conn() as conn:
        conn.execute(
            "UPDATE kreditorer SET navn = ?, cvr = ?, adresse = ?, email = ?, iban = ?, bic = ?, "
            "standard_kontonr = ? WHERE id = ?",
            (navn, cvr, adresse, email, iban, bic, standard_kontonr, kreditor_id),
        )
        _log(conn, bruger, "opdater_kreditor", "kreditor", kreditor_id, {"navn": navn, "cvr": cvr})


# ── Kontering: regler + selvlærende forslag ─────────────────────────────

def opret_konteringsregel(kreditor_id: int, match_tekst: str, kontonr: str,
                           beskrivelse: Optional[str] = None) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO konteringsregler (kreditor_id, match_tekst, kontonr, beskrivelse) VALUES (?, ?, ?, ?)",
            (kreditor_id, match_tekst, kontonr, beskrivelse),
        )
        return cur.lastrowid


def hent_konteringsregler(kreditor_id: Optional[int] = None) -> List[Dict[str, Any]]:
    with _conn() as conn:
        if kreditor_id:
            rows = conn.execute("""
                SELECT r.*, k.navn AS konto_navn FROM konteringsregler r
                JOIN kontoplan k ON k.kontonr = r.kontonr
                WHERE r.kreditor_id = ? ORDER BY r.id
            """, (kreditor_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT r.*, k.navn AS konto_navn FROM konteringsregler r
                JOIN kontoplan k ON k.kontonr = r.kontonr
                ORDER BY r.kreditor_id, r.id
            """).fetchall()
        return [dict(r) for r in rows]


def slet_konteringsregel(regel_id: int, bruger: str):
    with _conn() as conn:
        conn.execute("DELETE FROM konteringsregler WHERE id = ?", (regel_id,))
        _log(conn, bruger, "slet_konteringsregel", "konteringsregel", regel_id, {})


def foreslaa_kontering(kreditor_id: int, soegetekst: str) -> Optional[str]:
    """Foreslår en kontonr til et nyt bilag fra en kendt kreditor, i prioriteret rækkefølge:
    1) En konteringsregel hvis nøgleord findes i fakturateksten (variant — samme leverandør,
       forskellig konto afhængigt af hvad fakturaen indeholder).
    2) Leverandørens faste standardkonto (kreditorer.standard_kontonr), hvis sat.
    3) Den konto der historisk er brugt oftest for denne kreditor — selvlærende, kræver
       ingen opsætning, bliver mere præcis jo flere bilag der bogføres."""
    tekst = (soegetekst or "").lower()
    for regel in hent_konteringsregler(kreditor_id):
        if regel["match_tekst"].lower() in tekst:
            return regel["kontonr"]

    with _conn() as conn:
        kreditor = conn.execute(
            "SELECT standard_kontonr FROM kreditorer WHERE id = ?", (kreditor_id,)
        ).fetchone()
        if kreditor and kreditor["standard_kontonr"]:
            return kreditor["standard_kontonr"]

        hyppigst = conn.execute("""
            SELECT kontonr, COUNT(*) c FROM bilag
            WHERE kreditor_id = ? AND status = 'bogfoert' AND kontonr IS NOT NULL AND kontonr != ''
            GROUP BY kontonr ORDER BY c DESC LIMIT 1
        """, (kreditor_id,)).fetchone()
        return hyppigst["kontonr"] if hyppigst else None


def godkend_og_bogfoer_bilag(bilag_id: int, bruger: str, felter: Dict[str, Any]) -> int:
    """Godkend et bilag (med evt. rettede felter fra brugeren), opret/find kreditor,
    bogfør balanceret postering og åbn en kreditorpost. Returnerer postering_id."""
    bilag = hent_et_bilag(bilag_id)
    if not bilag:
        raise ValueError("Bilag findes ikke")
    if bilag["status"] not in ("afventer",):
        raise ValueError(f"Bilag har status '{bilag['status']}' — kan ikke godkendes igen")

    beloeb_ex_moms = round(float(felter.get("beloeb_ex_moms", 0) or 0), 2)
    moms_beloeb = round(float(felter.get("moms_beloeb", 0) or 0), 2)
    beloeb_total = round(float(felter.get("beloeb_total", beloeb_ex_moms + moms_beloeb) or 0), 2)
    kontonr = felter.get("kontonr") or "4000"
    navn = felter.get("leverandoer_navn") or "Ukendt leverandør"
    cvr = felter.get("leverandoer_cvr") or None
    adresse = felter.get("leverandoer_adresse") or None

    kreditor_id = find_or_create_kreditor(navn, cvr, adresse)

    linjer = [{"kontonr": kontonr, "debet": beloeb_ex_moms, "kredit": 0}]
    if moms_beloeb:
        linjer.append({"kontonr": "6960", "debet": moms_beloeb, "kredit": 0})
    linjer.append({
        "kontonr": "6800", "debet": 0, "kredit": beloeb_total,
        "modpart_type": "kreditor", "modpart_id": kreditor_id,
    })

    tekst = f"{navn} — faktura {felter.get('fakturanr') or bilag_id}"
    dato = felter.get("fakturadato") or date.today().isoformat()
    postering_id = bogfoer_postering(dato, tekst, bruger, linjer, bilag_id=bilag_id)

    with _conn() as conn:
        conn.execute(
            "INSERT INTO kreditor_poster (kreditor_id, bilag_id, fakturanr, dato, forfaldsdato, "
            "beloeb, restbeloeb, postering_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kreditor_id, bilag_id, felter.get("fakturanr"), dato, felter.get("forfaldsdato"),
             beloeb_total, beloeb_total, postering_id),
        )
        conn.execute(
            "UPDATE bilag SET status = 'bogfoert', godkendt_af = ?, godkendt_ts = datetime('now','localtime'), "
            "leverandoer_navn = ?, leverandoer_cvr = ?, fakturanr = ?, fakturadato = ?, forfaldsdato = ?, "
            "beloeb_ex_moms = ?, moms_beloeb = ?, beloeb_total = ?, kontonr = ?, kreditor_id = ? WHERE id = ?",
            (bruger, navn, cvr, felter.get("fakturanr"), dato, felter.get("forfaldsdato"),
             beloeb_ex_moms, moms_beloeb, beloeb_total, kontonr, kreditor_id, bilag_id),
        )
        _log(conn, bruger, "godkend_bilag", "bilag", bilag_id, felter)
    return postering_id


# ── Debitor ──────────────────────────────────────────────────────────────

def opret_debitor(navn: str, cvr: Optional[str] = None, adresse: Optional[str] = None,
                   email: Optional[str] = None) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO debitorer (navn, cvr, adresse, email) VALUES (?, ?, ?, ?)",
            (navn, cvr, adresse, email),
        )
        return cur.lastrowid


def hent_debitorer() -> List[Dict[str, Any]]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM debitorer ORDER BY navn").fetchall()]


def hent_en_debitor(debitor_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM debitorer WHERE id = ?", (debitor_id,)).fetchone()
        return dict(r) if r else None


def opdater_debitor(debitor_id: int, bruger: str, navn: str, cvr: Optional[str] = None,
                     adresse: Optional[str] = None, email: Optional[str] = None):
    with _conn() as conn:
        conn.execute(
            "UPDATE debitorer SET navn = ?, cvr = ?, adresse = ?, email = ? WHERE id = ?",
            (navn, cvr, adresse, email, debitor_id),
        )
        _log(conn, bruger, "opdater_debitor", "debitor", debitor_id, {"navn": navn, "cvr": cvr})


def opret_debitor_faktura(debitor_id: int, bruger: str, fakturanr: str, dato: str,
                           forfaldsdato: str, beloeb_ex_moms: float, moms_beloeb: float,
                           kontonr: str = "1000") -> int:
    beloeb_total = round(beloeb_ex_moms + moms_beloeb, 2)
    linjer = [
        {"kontonr": "6900", "debet": beloeb_total, "kredit": 0,
         "modpart_type": "debitor", "modpart_id": debitor_id},
        {"kontonr": kontonr, "debet": 0, "kredit": round(beloeb_ex_moms, 2)},
    ]
    if moms_beloeb:
        linjer.append({"kontonr": "6961", "debet": 0, "kredit": round(moms_beloeb, 2)})

    debitor = next((d for d in hent_debitorer() if d["id"] == debitor_id), None)
    navn = debitor["navn"] if debitor else f"debitor #{debitor_id}"
    tekst = f"{navn} — faktura {fakturanr}"
    postering_id = bogfoer_postering(dato, tekst, bruger, linjer)

    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO debitor_poster (debitor_id, fakturanr, dato, forfaldsdato, beloeb, restbeloeb, postering_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (debitor_id, fakturanr, dato, forfaldsdato, beloeb_total, beloeb_total, postering_id),
        )
        return cur.lastrowid


# ── Åbne poster + aldersfordeling ───────────────────────────────────────

def _aldersgruppe(forfaldsdato: Optional[str]) -> str:
    if not forfaldsdato:
        return "ukendt"
    try:
        dage = (date.today() - date.fromisoformat(forfaldsdato)).days
    except ValueError:
        return "ukendt"
    if dage < 0:
        return "ikke forfalden"
    if dage <= 30:
        return "1-30 dage"
    if dage <= 60:
        return "31-60 dage"
    if dage <= 90:
        return "61-90 dage"
    return "90+ dage"


def hent_aabne_debitor_poster() -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT dp.*, d.navn AS debitor_navn
            FROM debitor_poster dp JOIN debitorer d ON d.id = dp.debitor_id
            WHERE dp.status != 'udlignet'
            ORDER BY dp.forfaldsdato
        """).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            r["aldersgruppe"] = _aldersgruppe(r["forfaldsdato"])
        return out


def hent_aabne_kreditor_poster() -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT kp.*, k.navn AS kreditor_navn
            FROM kreditor_poster kp JOIN kreditorer k ON k.id = kp.kreditor_id
            WHERE kp.status NOT IN ('udlignet', 'annulleret')
            ORDER BY kp.forfaldsdato
        """).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            r["aldersgruppe"] = _aldersgruppe(r["forfaldsdato"])
        return out


def hent_dashboard_kpi() -> Dict[str, Any]:
    with _conn() as conn:
        kred = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(restbeloeb),0) s FROM kreditor_poster WHERE status NOT IN ('udlignet', 'annulleret')"
        ).fetchone()
        deb = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(restbeloeb),0) s FROM debitor_poster WHERE status != 'udlignet'"
        ).fetchone()
        afventer = conn.execute("SELECT COUNT(*) c FROM bilag WHERE status = 'afventer'").fetchone()
        bank = conn.execute("SELECT COALESCE(SUM(beloeb),0) s FROM banktransaktioner").fetchone()
        maaned = conn.execute("""
            SELECT COALESCE(SUM(beloeb_total),0) s, COUNT(*) c FROM bilag
            WHERE status = 'bogfoert' AND strftime('%Y-%m', godkendt_ts) = strftime('%Y-%m','now','localtime')
        """).fetchone()
        return {
            "kreditor_sum": kred["s"], "kreditor_antal": kred["c"],
            "debitor_sum": deb["s"], "debitor_antal": deb["c"],
            "bilag_afventer": afventer["c"],
            "banksaldo": bank["s"],
            "maaned_sum": maaned["s"], "maaned_antal": maaned["c"],
        }


# ── Banktransaktioner (simuleret indlæsning) + match ────────────────────

def indlæs_banktransaktioner(linjer: List[Dict[str, Any]], kilde: str = "simulation") -> int:
    """linjer: [{"dato": "YYYY-MM-DD", "beloeb": float, "tekst": str}, ...]
    Positivt beløb = indbetaling, negativt = udbetaling. Returnerer antal indlæst."""
    antal = 0
    with _conn() as conn:
        for l in linjer:
            conn.execute(
                "INSERT INTO banktransaktioner (dato, beloeb, tekst, match_status) "
                "VALUES (?, ?, ?, 'uafklaret')",
                (l["dato"], l["beloeb"], l.get("tekst", "")),
            )
            antal += 1
        _log(conn, "system", "indlaes_banktransaktioner", "banktransaktion", None,
             {"antal": antal, "kilde": kilde})
    return antal


def hent_banktransaktioner(match_status: Optional[str] = None, aar: Optional[int] = None,
                            maaned: Optional[int] = None) -> List[Dict[str, Any]]:
    with _conn() as conn:
        vilkaar = []
        params: List[Any] = []
        if match_status:
            vilkaar.append("match_status = ?")
            params.append(match_status)
        if aar is not None:
            vilkaar.append("dato LIKE ?")
            params.append(f"{aar:04d}-{maaned:02d}-%" if maaned else f"{aar:04d}-%")
        where = f"WHERE {' AND '.join(vilkaar)}" if vilkaar else ""
        rows = conn.execute(
            f"SELECT * FROM banktransaktioner {where} ORDER BY dato DESC, id DESC", params
        ).fetchall()
        return [dict(r) for r in rows]


def indlæs_eksterne_banktransaktioner(bank_forbindelse_id: int, transaktioner: List[Dict[str, Any]]) -> int:
    """transaktioner: [{"ekstern_id": str, "dato": "YYYY-MM-DD", "beloeb": float, "tekst": str}, ...]
    Dubletter (samme ekstern_id) springes stille over. Returnerer antal NYE transaktioner indlæst."""
    antal = 0
    with _conn() as conn:
        for t in transaktioner:
            try:
                conn.execute(
                    "INSERT INTO banktransaktioner (bank_forbindelse_id, ekstern_id, dato, beloeb, tekst, match_status) "
                    "VALUES (?, ?, ?, ?, ?, 'uafklaret')",
                    (bank_forbindelse_id, t["ekstern_id"], t["dato"], t["beloeb"], t.get("tekst", "")),
                )
                antal += 1
            except sqlite3.IntegrityError:
                pass  # allerede indlæst tidligere (samme ekstern_id)
        if antal:
            _log(conn, "system", "indlaes_eksterne_banktransaktioner", "bank_forbindelse", bank_forbindelse_id, {"antal": antal})
    return antal


def opret_bank_forbindelse(institution_id: str, institution_navn: str, account_id: str, iban: Optional[str],
                            requisition_id: str, consent_expires_ts: str) -> int:
    with _conn() as conn:
        conn.execute("""
            INSERT INTO bank_forbindelser (institution_id, institution_navn, account_id, iban, requisition_id, consent_expires_ts, status)
            VALUES (?, ?, ?, ?, ?, ?, 'aktiv')
            ON CONFLICT(account_id) DO UPDATE SET
                requisition_id = excluded.requisition_id, consent_expires_ts = excluded.consent_expires_ts, status = 'aktiv'
        """, (institution_id, institution_navn, account_id, iban, requisition_id, consent_expires_ts))
        return conn.execute("SELECT id FROM bank_forbindelser WHERE account_id = ?", (account_id,)).fetchone()["id"]


def hent_bank_forbindelser() -> List[Dict[str, Any]]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM bank_forbindelser ORDER BY id").fetchall()]


def opdater_bank_saldo(forbindelse_id: int, saldo: Optional[float]):
    with _conn() as conn:
        conn.execute(
            "UPDATE bank_forbindelser SET saldo = ?, saldo_ts = datetime('now','localtime') WHERE id = ?",
            (saldo, forbindelse_id),
        )


def luk_bank_forbindelse(forbindelse_id: int, bruger: str):
    with _conn() as conn:
        conn.execute("UPDATE bank_forbindelser SET status = 'tilbagekaldt' WHERE id = ?", (forbindelse_id,))
        _log(conn, bruger, "luk_bank_forbindelse", "bank_forbindelse", forbindelse_id, {})


def _dage_mellem(a: str, b: str) -> Optional[int]:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except (ValueError, TypeError):
        return None


def foreslaa_matches(transaktion: Dict[str, Any], maks: int = 3) -> List[Dict[str, Any]]:
    """Foreslår kandidater fra åbne debitor-/kreditorposter til en banktransaktion.
    Kræver altid præcist beløbsmatch (0 kr tolerance); score afgør rækkefølgen.
    Udligner aldrig automatisk — kalder skal selv godkende via godkend_bank_match()."""
    beloeb = transaktion["beloeb"]
    tekst_norm = (transaktion.get("tekst") or "").lower()
    dato = transaktion["dato"]

    if beloeb < 0:
        modpart_type = "kreditor"
        kandidater = hent_aabne_kreditor_poster()
        navn_felt = "kreditor_navn"
    else:
        modpart_type = "debitor"
        kandidater = hent_aabne_debitor_poster()
        navn_felt = "debitor_navn"

    forslag = []
    for k in kandidater:
        if round(abs(beloeb) - k["restbeloeb"], 2) != 0:
            continue
        score = 0
        begrundelser = []

        if k.get("fakturanr") and k["fakturanr"].lower() in tekst_norm:
            score += 100
            begrundelser.append(f"fakturanr '{k['fakturanr']}' fundet i posteringsteksten")

        navn_score = fuzz.token_sort_ratio(tekst_norm, (k.get(navn_felt) or "").lower())
        if navn_score >= 70:
            score += navn_score
            begrundelser.append(f"navnematch {navn_score:.0f}%")

        dage = _dage_mellem(dato, k.get("forfaldsdato")) if k.get("forfaldsdato") else None
        if dage is not None and dage <= 45:
            score += max(0, 30 - dage)
            begrundelser.append(f"{dage} dage fra forfaldsdato")

        if score > 0:
            forslag.append({
                "modpart_type": modpart_type,
                "post_id": k["id"],
                "navn": k.get(navn_felt),
                "fakturanr": k.get("fakturanr"),
                "restbeloeb": k["restbeloeb"],
                "score": score,
                "begrundelse": "; ".join(begrundelser) or "beløb matcher",
            })

    # Uploadede, endnu ikke bogførte bilag — så en leverandørfaktura der ligger og venter
    # automatisk bliver foreslået, i stedet for at brugeren selv skal huske at bogføre den
    # først og bagefter matche den. Her er selv et rent beløbsmatch et stærkt signal (få
    # kandidater i puljen), så scoren sættes til mindst 1 i stedet for at kræve et navn/dato-hit.
    if beloeb < 0:
        for b in hent_bilag(status="afventer"):
            if round(abs(beloeb) - (b.get("beloeb_total") or 0), 2) != 0:
                continue
            score = 0
            begrundelser = []

            if b.get("fakturanr") and b["fakturanr"].lower() in tekst_norm:
                score += 100
                begrundelser.append(f"fakturanr '{b['fakturanr']}' fundet i posteringsteksten")

            navn_score = fuzz.token_sort_ratio(tekst_norm, (b.get("leverandoer_navn") or "").lower())
            if navn_score >= 70:
                score += navn_score
                begrundelser.append(f"navnematch {navn_score:.0f}%")

            dage = _dage_mellem(dato, b["fakturadato"]) if b.get("fakturadato") else None
            if dage is not None and dage <= 45:
                score += max(0, 30 - dage)
                begrundelser.append(f"{dage} dage fra fakturadato")

            forslag.append({
                "modpart_type": "bilag",
                "post_id": b["id"],
                "navn": b.get("leverandoer_navn") or "Ukendt leverandør",
                "fakturanr": b.get("fakturanr"),
                "restbeloeb": b["beloeb_total"],
                "score": max(score, 1),
                "begrundelse": "; ".join(begrundelser) or "beløb matcher et uploadet, ikke-bogført bilag",
            })

    forslag.sort(key=lambda f: -f["score"])
    return forslag[:maks]


def godkend_bank_match(transaktion_id: int, modpart_type: str, post_id: int, bruger: str) -> int:
    """Bruger har bekræftet et matchforslag — bogfør udligningspostering og luk posten.
    Hvis modpart_type er "bilag" bogføres fakturaen først (med bilagets egne, evt. AI-udtrukne
    felter — ingen manuel indtastning), og selve betalingen udlignes derefter mod den
    kreditorpost det gav anledning til. Returnerer postering_id for selve udligningen."""
    with _conn() as conn:
        tx = conn.execute("SELECT * FROM banktransaktioner WHERE id = ?", (transaktion_id,)).fetchone()
        if not tx:
            raise ValueError("Banktransaktion findes ikke")
        if tx["match_status"] == "godkendt":
            raise ValueError("Banktransaktionen er allerede matchet")

    if modpart_type == "bilag":
        bilag = hent_et_bilag(post_id)
        if not bilag:
            raise ValueError("Bilag findes ikke")
        if bilag["status"] != "afventer":
            raise ValueError(f"Bilag har status '{bilag['status']}' — kan ikke matches automatisk")
        godkend_og_bogfoer_bilag(post_id, bruger, bilag)
        with _conn() as conn:
            kp = conn.execute(
                "SELECT id FROM kreditor_poster WHERE bilag_id = ? ORDER BY id DESC LIMIT 1", (post_id,)
            ).fetchone()
        modpart_type = "kreditor"
        post_id = kp["id"]

    with _conn() as conn:
        if modpart_type == "kreditor":
            post = conn.execute(
                "SELECT kp.*, k.navn AS navn FROM kreditor_poster kp JOIN kreditorer k ON k.id = kp.kreditor_id "
                "WHERE kp.id = ?", (post_id,),
            ).fetchone()
            if not post:
                raise ValueError("Kreditorpost findes ikke")
            if post["status"] in ("udlignet", "annulleret"):
                raise ValueError("Kreditorposten er ikke længere åben (allerede udlignet eller annulleret)")
            beloeb = post["restbeloeb"]
            linjer = [
                {"kontonr": "6800", "debet": beloeb, "kredit": 0,
                 "modpart_type": "kreditor", "modpart_id": post["kreditor_id"]},
                {"kontonr": "6750", "debet": 0, "kredit": beloeb},
            ]
            tekst = f"Betaling til {post['navn']} — faktura {post['fakturanr'] or post_id}"
        elif modpart_type == "debitor":
            post = conn.execute(
                "SELECT dp.*, d.navn AS navn FROM debitor_poster dp JOIN debitorer d ON d.id = dp.debitor_id "
                "WHERE dp.id = ?", (post_id,),
            ).fetchone()
            if not post:
                raise ValueError("Debitorpost findes ikke")
            if post["status"] in ("udlignet", "annulleret"):
                raise ValueError("Debitorposten er ikke længere åben (allerede udlignet eller annulleret)")
            beloeb = post["restbeloeb"]
            linjer = [
                {"kontonr": "6750", "debet": beloeb, "kredit": 0},
                {"kontonr": "6900", "debet": 0, "kredit": beloeb,
                 "modpart_type": "debitor", "modpart_id": post["debitor_id"]},
            ]
            tekst = f"Indbetaling fra {post['navn']} — faktura {post['fakturanr'] or post_id}"
        else:
            raise ValueError("Ukendt modpart_type")

    postering_id = bogfoer_postering(tx["dato"], tekst, bruger, linjer)

    with _conn() as conn:
        tabel = "kreditor_poster" if modpart_type == "kreditor" else "debitor_poster"
        conn.execute(f"UPDATE {tabel} SET status = 'udlignet', restbeloeb = 0 WHERE id = ?", (post_id,))
        conn.execute(
            "UPDATE banktransaktioner SET match_status = 'godkendt', matchet_type = ?, matchet_id = ? WHERE id = ?",
            (modpart_type, post_id, transaktion_id),
        )
        _log(conn, bruger, "godkend_bank_match", "banktransaktion", transaktion_id,
             {"modpart_type": modpart_type, "post_id": post_id, "postering_id": postering_id})
    return postering_id


def ignorer_banktransaktion(transaktion_id: int, bruger: str):
    with _conn() as conn:
        conn.execute(
            "UPDATE banktransaktioner SET match_status = 'ignoreret' WHERE id = ?", (transaktion_id,)
        )
        _log(conn, bruger, "ignorer_banktransaktion", "banktransaktion", transaktion_id, {})


def bogfoer_bank_direkte(transaktion_id: int, kontonr: str, bruger: str) -> int:
    """Bogfør en banktransaktion direkte mod en valgt konto uden om debitor/kreditor/bilag —
    til poster uden faktura (gebyrer, renter og lignende direkte bankposteringer).
    Returnerer postering_id."""
    with _conn() as conn:
        tx = conn.execute("SELECT * FROM banktransaktioner WHERE id = ?", (transaktion_id,)).fetchone()
        if not tx:
            raise ValueError("Banktransaktion findes ikke")
        if tx["match_status"] == "godkendt":
            raise ValueError("Banktransaktionen er allerede matchet")
        if not conn.execute("SELECT 1 FROM kontoplan WHERE kontonr = ?", (kontonr,)).fetchone():
            raise ValueError("Ukendt kontonummer")

    beloeb = abs(tx["beloeb"])
    if tx["beloeb"] < 0:
        linjer = [
            {"kontonr": kontonr, "debet": beloeb, "kredit": 0},
            {"kontonr": "6750", "debet": 0, "kredit": beloeb},
        ]
    else:
        linjer = [
            {"kontonr": "6750", "debet": beloeb, "kredit": 0},
            {"kontonr": kontonr, "debet": 0, "kredit": beloeb},
        ]
    tekst = tx["tekst"] or "Direkte bankpostering"
    postering_id = bogfoer_postering(tx["dato"], tekst, bruger, linjer)

    with _conn() as conn:
        conn.execute(
            "UPDATE banktransaktioner SET match_status = 'godkendt', matchet_type = 'direkte', "
            "matchet_id = ? WHERE id = ?",
            (postering_id, transaktion_id),
        )
        _log(conn, bruger, "bogfoer_bank_direkte", "banktransaktion", transaktion_id,
             {"kontonr": kontonr, "postering_id": postering_id})
    return postering_id


# ── Rapporter: resultatopgørelse, balance, moms ─────────────────────────

def hent_resultatopgoerelse(fra: str, til: str) -> Dict[str, Any]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT pl.kontonr, k.navn, k.kontotype,
                   COALESCE(SUM(pl.debet), 0) sum_debet, COALESCE(SUM(pl.kredit), 0) sum_kredit
            FROM posteringslinjer pl
            JOIN posteringer p ON p.id = pl.postering_id
            JOIN kontoplan k ON k.kontonr = pl.kontonr
            WHERE p.dato BETWEEN ? AND ? AND k.kontotype IN ('omsaetning', 'omkostning')
            GROUP BY pl.kontonr
            ORDER BY k.kontotype DESC, pl.kontonr
        """, (fra, til)).fetchall()

    omsaetning, omkostning = [], []
    sum_omsaetning = sum_omkostning = 0.0
    for r in rows:
        if r["kontotype"] == "omsaetning":
            beloeb = round(r["sum_kredit"] - r["sum_debet"], 2)
            if beloeb:
                omsaetning.append({"kontonr": r["kontonr"], "navn": r["navn"], "beloeb": beloeb})
                sum_omsaetning += beloeb
        else:
            beloeb = round(r["sum_debet"] - r["sum_kredit"], 2)
            if beloeb:
                omkostning.append({"kontonr": r["kontonr"], "navn": r["navn"], "beloeb": beloeb})
                sum_omkostning += beloeb

    return {
        "fra": fra, "til": til,
        "omsaetning": omsaetning, "sum_omsaetning": round(sum_omsaetning, 2),
        "omkostning": omkostning, "sum_omkostning": round(sum_omkostning, 2),
        "resultat": round(sum_omsaetning - sum_omkostning, 2),
    }


def hent_balance(til: str) -> Dict[str, Any]:
    """Aktiver = Passiver + Egenkapital. Egenkapital vises som en beregnet, balancerende
    post (akkumuleret resultat inkl. indeværende periode), da systemet ikke (endnu) laver
    formelle periodeafslutninger til en bogført egenkapitalkonto."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT pl.kontonr, k.navn, k.kontotype,
                   COALESCE(SUM(pl.debet), 0) sum_debet, COALESCE(SUM(pl.kredit), 0) sum_kredit
            FROM posteringslinjer pl
            JOIN posteringer p ON p.id = pl.postering_id
            JOIN kontoplan k ON k.kontonr = pl.kontonr
            WHERE p.dato <= ? AND k.kontotype IN ('aktiv', 'passiv', 'moms')
            GROUP BY pl.kontonr
            ORDER BY pl.kontonr
        """, (til,)).fetchall()

    aktiver, passiver = [], []
    sum_aktiver = sum_passiver = 0.0
    for r in rows:
        # 6960 Købsmoms er et tilgodehavende hos SKAT (aktiv), 6961 Salgsmoms er en gæld til SKAT (passiv)
        er_aktiv = r["kontotype"] == "aktiv" or r["kontonr"] == "6960"
        if er_aktiv:
            beloeb = round(r["sum_debet"] - r["sum_kredit"], 2)
            if beloeb:
                aktiver.append({"kontonr": r["kontonr"], "navn": r["navn"], "beloeb": beloeb})
                sum_aktiver += beloeb
        else:
            beloeb = round(r["sum_kredit"] - r["sum_debet"], 2)
            if beloeb:
                passiver.append({"kontonr": r["kontonr"], "navn": r["navn"], "beloeb": beloeb})
                sum_passiver += beloeb

    egenkapital = round(sum_aktiver - sum_passiver, 2)
    passiver.append({"kontonr": None, "navn": "Egenkapital (akkumuleret resultat)", "beloeb": egenkapital})

    return {
        "til": til,
        "aktiver": aktiver, "sum_aktiver": round(sum_aktiver, 2),
        "passiver": passiver, "sum_passiver": round(sum_aktiver, 2),  # balancerer pr. konstruktion
    }


def _moms_betalingsfrist(aar: int, m_til: int) -> str:
    """Standardfristen for kvartalsafregnende virksomheder: den 1. i den 3. måned efter
    afgiftsperiodens udløb (fx Q1 der slutter 31. marts -> frist 1. juni). Rykkes IKKE
    automatisk for weekend/helligdag her — SKAT flytter i så fald til næste bankdag."""
    frist_maaned = m_til + 3
    frist_aar = aar
    if frist_maaned > 12:
        frist_maaned -= 12
        frist_aar += 1
    return f"{frist_aar}-{frist_maaned:02d}-01"


def hent_momsopgoerelse(aar: int, kvartal: int) -> Dict[str, Any]:
    maaneder = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
    m_fra, m_til = maaneder[kvartal]
    fra = f"{aar}-{m_fra:02d}-01"
    sidste_dag = calendar.monthrange(aar, m_til)[1]
    til = f"{aar}-{m_til:02d}-{sidste_dag:02d}"
    betalingsfrist = _moms_betalingsfrist(aar, m_til)

    with _conn() as conn:
        salgsmoms = conn.execute("""
            SELECT COALESCE(SUM(pl.kredit), 0) - COALESCE(SUM(pl.debet), 0) s
            FROM posteringslinjer pl JOIN posteringer p ON p.id = pl.postering_id
            WHERE pl.kontonr = '6961' AND p.dato BETWEEN ? AND ?
        """, (fra, til)).fetchone()["s"]
        koebsmoms = conn.execute("""
            SELECT COALESCE(SUM(pl.debet), 0) - COALESCE(SUM(pl.kredit), 0) s
            FROM posteringslinjer pl JOIN posteringer p ON p.id = pl.postering_id
            WHERE pl.kontonr = '6960' AND p.dato BETWEEN ? AND ?
        """, (fra, til)).fetchone()["s"]

    return {
        "aar": aar, "kvartal": kvartal, "fra": fra, "til": til,
        "salgsmoms": round(salgsmoms, 2), "koebsmoms": round(koebsmoms, 2),
        "momstilsvar": round(salgsmoms - koebsmoms, 2),
        "betalingsfrist": betalingsfrist,
    }
