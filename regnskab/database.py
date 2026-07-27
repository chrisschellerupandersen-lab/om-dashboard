import sqlite3
import os
import json
import hashlib
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
                iban               TEXT,
                requisition_id     TEXT UNIQUE,
                consent_expires_ts TEXT,
                status             TEXT DEFAULT 'aktiv'
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
        ("1000", "Varesalg",                      "omsaetning", "salgsmoms"),
        ("2000", "Vareforbrug",                    "omkostning", "koebsmoms"),
        ("3000", "Lokaleomkostninger",             "omkostning", "koebsmoms"),
        ("3400", "Personaleomkostninger",          "omkostning", None),
        ("3500", "Kontorhold & administration",    "omkostning", "koebsmoms"),
        ("4000", "Diverse driftsomkostninger",     "omkostning", "koebsmoms"),
        ("6750", "Bank",                           "aktiv",      None),
        ("6900", "Debitorer (tilgodehavender)",    "aktiv",      None),
        ("6800", "Kreditorer (leverandoergaeld)",  "passiv",     None),
        ("6960", "Koebsmoms",                      "moms",       None),
        ("6961", "Salgsmoms",                      "moms",       None),
        ("9999", "Egenkapital / aabningsbalance",  "status",     None),
    ]
    for kontonr, navn, kontotype, moms_kode in konti:
        conn.execute(
            "INSERT OR IGNORE INTO kontoplan (kontonr, navn, kontotype, moms_kode) VALUES (?, ?, ?, ?)",
            (kontonr, navn, kontotype, moms_kode),
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


def hent_posteringer(limit: int = 200) -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM posteringer ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            linjer = conn.execute(
                "SELECT * FROM posteringslinjer WHERE postering_id = ? ORDER BY id", (r["id"],)
            ).fetchall()
            out.append({**dict(r), "linjer": [dict(l) for l in linjer]})
        return out


def hent_kontoplan() -> List[Dict[str, Any]]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM kontoplan WHERE aktiv = 1 ORDER BY kontonr"
        ).fetchall()]


# ── Bilag ────────────────────────────────────────────────────────────────

def opret_bilag(fil_sti: str, fil_sha256: str, ai_raw_json: Optional[str] = None,
                 ai_model: Optional[str] = None, felter: Optional[Dict[str, Any]] = None) -> Optional[int]:
    felter = felter or {}
    with _conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO bilag (type, fil_sti, fil_sha256, ai_raw_json, ai_model, "
                "leverandoer_cvr, leverandoer_navn, fakturanr, fakturadato, forfaldsdato, "
                "beloeb_ex_moms, moms_beloeb, beloeb_total, kontonr) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    felter.get("type", "leverandoerfaktura"), fil_sti, fil_sha256, ai_raw_json, ai_model,
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
                    email: Optional[str] = None, iban: Optional[str] = None, bic: Optional[str] = None) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO kreditorer (navn, cvr, adresse, email, iban, bic) VALUES (?, ?, ?, ?, ?, ?)",
            (navn, cvr, adresse, email, iban, bic),
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
                      iban: Optional[str] = None, bic: Optional[str] = None):
    with _conn() as conn:
        conn.execute(
            "UPDATE kreditorer SET navn = ?, cvr = ?, adresse = ?, email = ?, iban = ?, bic = ? WHERE id = ?",
            (navn, cvr, adresse, email, iban, bic, kreditor_id),
        )
        _log(conn, bruger, "opdater_kreditor", "kreditor", kreditor_id, {"navn": navn, "cvr": cvr})


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
            WHERE kp.status != 'udlignet'
            ORDER BY kp.forfaldsdato
        """).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            r["aldersgruppe"] = _aldersgruppe(r["forfaldsdato"])
        return out


def hent_dashboard_kpi() -> Dict[str, Any]:
    with _conn() as conn:
        kred = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(restbeloeb),0) s FROM kreditor_poster WHERE status != 'udlignet'"
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


def hent_banktransaktioner(match_status: Optional[str] = None) -> List[Dict[str, Any]]:
    with _conn() as conn:
        if match_status:
            rows = conn.execute(
                "SELECT * FROM banktransaktioner WHERE match_status = ? ORDER BY dato DESC, id DESC",
                (match_status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM banktransaktioner ORDER BY dato DESC, id DESC"
            ).fetchall()
        return [dict(r) for r in rows]


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

    forslag.sort(key=lambda f: -f["score"])
    return forslag[:maks]


def godkend_bank_match(transaktion_id: int, modpart_type: str, post_id: int, bruger: str) -> int:
    """Bruger har bekræftet et matchforslag — bogfør udligningspostering og luk posten.
    Returnerer postering_id."""
    with _conn() as conn:
        tx = conn.execute("SELECT * FROM banktransaktioner WHERE id = ?", (transaktion_id,)).fetchone()
        if not tx:
            raise ValueError("Banktransaktion findes ikke")
        if tx["match_status"] == "godkendt":
            raise ValueError("Banktransaktionen er allerede matchet")

        if modpart_type == "kreditor":
            post = conn.execute(
                "SELECT kp.*, k.navn AS navn FROM kreditor_poster kp JOIN kreditorer k ON k.id = kp.kreditor_id "
                "WHERE kp.id = ?", (post_id,),
            ).fetchone()
            if not post:
                raise ValueError("Kreditorpost findes ikke")
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
