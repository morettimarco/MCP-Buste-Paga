from __future__ import annotations

import pathlib
import sqlite3
from decimal import Decimal

from .models import FooterData, HeaderData, ParsedPayslip, VoceCorpoBusta

DEFAULT_DB_PATH = pathlib.Path.home() / ".mcp-buste-paga" / "buste_paga.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS aziende (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    codice_fiscale TEXT UNIQUE NOT NULL,
    pos_inps TEXT,
    pos_inail TEXT
);

CREATE TABLE IF NOT EXISTS dipendenti (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codice TEXT NOT NULL,
    nome TEXT NOT NULL,
    cognome TEXT NOT NULL,
    codice_fiscale TEXT UNIQUE NOT NULL,
    data_nascita TEXT,
    data_assunzione TEXT,
    qualifica TEXT,
    livello TEXT,
    contratto_codice TEXT,
    contratto_nome TEXT,
    azienda_id INTEGER NOT NULL REFERENCES aziende(id)
);

CREATE TABLE IF NOT EXISTS buste_paga (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dipendente_id INTEGER NOT NULL REFERENCES dipendenti(id),
    mese INTEGER NOT NULL,
    anno INTEGER NOT NULL,
    elementi_retributivi_totale REAL,
    totale_competenze REAL,
    totale_ritenute REAL,
    arr_precedente REAL,
    arr_attuale REAL,
    netto_a_pagare REAL,
    gg_detr INTEGER,
    fonte_azi REAL,
    banca_ore REAL,
    imp_fiscale REAL,
    impos_lorda REAL,
    detr_godute REAL,
    impos_netta REAL,
    fdo_tfr_ap REAL,
    fdo_tfr_a REAL,
    lordo_anno REAL,
    inps_anno REAL,
    iban TEXT,
    pdf_sha256 TEXT UNIQUE NOT NULL,
    pdf_filename TEXT,
    is_valid BOOLEAN DEFAULT 0,
    UNIQUE(dipendente_id, mese, anno)
);

CREATE TABLE IF NOT EXISTS voci_corpo_busta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    busta_id INTEGER NOT NULL REFERENCES buste_paga(id),
    riga_ordine INTEGER NOT NULL,
    assoggettamento TEXT,
    codice_voce TEXT NOT NULL,
    descrizione TEXT NOT NULL,
    ore_gg_num_perc REAL,
    dato_base REAL,
    dato_figurativo REAL,
    competenze REAL,
    ritenute REAL
);

CREATE INDEX IF NOT EXISTS idx_buste_paga_anno_mese ON buste_paga(anno, mese);
CREATE INDEX IF NOT EXISTS idx_voci_codice ON voci_corpo_busta(codice_voce);
CREATE INDEX IF NOT EXISTS idx_voci_descrizione ON voci_corpo_busta(descrizione);
"""


def _dec(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def init_db(db_path: pathlib.Path | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def upsert_azienda(conn: sqlite3.Connection, header: HeaderData) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO aziende (nome, codice_fiscale, pos_inps, pos_inail) VALUES (?, ?, ?, ?)",
        (header.azienda_nome, header.azienda_cf, header.pos_inps, header.pos_inail),
    )
    row = conn.execute(
        "SELECT id FROM aziende WHERE codice_fiscale = ?", (header.azienda_cf,)
    ).fetchone()
    return row["id"]


def upsert_dipendente(
    conn: sqlite3.Connection, header: HeaderData, azienda_id: int
) -> int:
    conn.execute(
        """INSERT OR IGNORE INTO dipendenti
        (codice, nome, cognome, codice_fiscale, data_nascita, data_assunzione,
         qualifica, livello, contratto_codice, contratto_nome, azienda_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            header.dipendente_codice,
            header.dipendente_nome,
            header.dipendente_cognome,
            header.dipendente_cf,
            header.data_nascita,
            header.data_assunzione,
            header.qualifica,
            header.livello,
            header.contratto_codice,
            header.contratto_nome,
            azienda_id,
        ),
    )
    row = conn.execute(
        "SELECT id FROM dipendenti WHERE codice_fiscale = ?", (header.dipendente_cf,)
    ).fetchone()
    return row["id"]


def insert_busta(
    conn: sqlite3.Connection,
    payslip: ParsedPayslip,
    dipendente_id: int,
    filename: str,
) -> int | None:
    """Insert a payslip. Returns busta_id, or None if duplicate (SHA or month)."""
    f = payslip.footer
    try:
        cur = conn.execute(
            """INSERT INTO buste_paga
            (dipendente_id, mese, anno, elementi_retributivi_totale,
             totale_competenze, totale_ritenute, arr_precedente, arr_attuale,
             netto_a_pagare, gg_detr, fonte_azi, banca_ore,
             imp_fiscale, impos_lorda, detr_godute, impos_netta,
             fdo_tfr_ap, fdo_tfr_a, lordo_anno, inps_anno,
             iban, pdf_sha256, pdf_filename, is_valid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dipendente_id,
                payslip.header.mese,
                payslip.header.anno,
                _dec(payslip.header.elementi_retributivi_totale),
                _dec(f.totale_competenze) if f else None,
                _dec(f.totale_ritenute) if f else None,
                _dec(f.arr_precedente) if f else None,
                _dec(f.arr_attuale) if f else None,
                _dec(f.netto_a_pagare) if f else None,
                f.gg_detr if f else None,
                _dec(f.fonte_azi) if f else None,
                _dec(f.banca_ore) if f else None,
                _dec(f.imp_fiscale) if f else None,
                _dec(f.impos_lorda) if f else None,
                _dec(f.detr_godute) if f else None,
                _dec(f.impos_netta) if f else None,
                _dec(f.fdo_tfr_ap) if f else None,
                _dec(f.fdo_tfr_a) if f else None,
                _dec(f.lordo_anno) if f else None,
                _dec(f.inps_anno) if f else None,
                f.iban if f else None,
                payslip.sha256,
                filename,
                payslip.is_valid,
            ),
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def insert_voci(
    conn: sqlite3.Connection, busta_id: int, voci: list[VoceCorpoBusta]
) -> None:
    for i, v in enumerate(voci):
        conn.execute(
            """INSERT INTO voci_corpo_busta
            (busta_id, riga_ordine, assoggettamento, codice_voce, descrizione,
             ore_gg_num_perc, dato_base, dato_figurativo, competenze, ritenute)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                busta_id,
                i,
                v.assoggettamento,
                v.codice_voce,
                v.descrizione,
                _dec(v.ore_gg_num_perc),
                _dec(v.dato_base),
                _dec(v.dato_figurativo),
                _dec(v.competenze),
                _dec(v.ritenute),
            ),
        )


# ── Query functions ──────────────────────────────────────────────────────────


def get_employee_info(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        """SELECT d.*, a.nome AS azienda_nome, a.codice_fiscale AS azienda_cf,
                  a.pos_inps, a.pos_inail,
                  (SELECT COUNT(*) FROM buste_paga WHERE dipendente_id = d.id) AS num_buste,
                  (SELECT MIN(anno * 100 + mese) FROM buste_paga WHERE dipendente_id = d.id) AS first_period,
                  (SELECT MAX(anno * 100 + mese) FROM buste_paga WHERE dipendente_id = d.id) AS last_period
           FROM dipendenti d JOIN aziende a ON d.azienda_id = a.id
           LIMIT 1"""
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    fp = d.pop("first_period", None)
    lp = d.pop("last_period", None)
    if fp:
        d["first_period"] = f"{fp % 100:02d}/{fp // 100}"
    if lp:
        d["last_period"] = f"{lp % 100:02d}/{lp // 100}"
    return d


def get_salary_history(
    conn: sqlite3.Connection, year: int | None = None, limit: int = 12
) -> list[dict]:
    sql = """SELECT mese, anno, elementi_retributivi_totale, totale_competenze,
                    totale_ritenute, netto_a_pagare, lordo_anno, is_valid
             FROM buste_paga"""
    params: list = []
    if year:
        sql += " WHERE anno = ?"
        params.append(year)
    sql += " ORDER BY anno DESC, mese DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_payslip_details(
    conn: sqlite3.Connection, mese: int, anno: int
) -> dict | None:
    busta = conn.execute(
        "SELECT * FROM buste_paga WHERE mese = ? AND anno = ?", (mese, anno)
    ).fetchone()
    if not busta:
        return None
    busta_dict = dict(busta)
    voci = conn.execute(
        """SELECT assoggettamento, codice_voce, descrizione,
                  ore_gg_num_perc, dato_base, dato_figurativo,
                  competenze, ritenute
           FROM voci_corpo_busta WHERE busta_id = ? ORDER BY riga_ordine""",
        (busta_dict["id"],),
    ).fetchall()
    busta_dict["voci"] = [dict(v) for v in voci]
    return busta_dict


def search_items(
    conn: sqlite3.Connection,
    keyword: str,
    start_year: int | None = None,
    end_year: int | None = None,
) -> dict:
    sql = """SELECT b.mese, b.anno, v.codice_voce, v.descrizione,
                    SUM(v.competenze) AS tot_competenze,
                    SUM(v.ritenute) AS tot_ritenute,
                    COUNT(*) AS occurrenze
             FROM voci_corpo_busta v
             JOIN buste_paga b ON v.busta_id = b.id
             WHERE v.descrizione LIKE ?"""
    params: list = [f"%{keyword}%"]
    if start_year:
        sql += " AND b.anno >= ?"
        params.append(start_year)
    if end_year:
        sql += " AND b.anno <= ?"
        params.append(end_year)
    sql += " GROUP BY b.anno, b.mese, v.codice_voce, v.descrizione ORDER BY b.anno DESC, b.mese DESC"

    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    grand_competenze = sum(r["tot_competenze"] or 0 for r in rows)
    grand_ritenute = sum(r["tot_ritenute"] or 0 for r in rows)

    return {
        "keyword": keyword,
        "results": rows,
        "grand_total_competenze": grand_competenze,
        "grand_total_ritenute": grand_ritenute,
    }
