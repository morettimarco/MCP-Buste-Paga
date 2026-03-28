from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class HeaderData:
    azienda_nome: str
    azienda_cf: str
    pos_inps: str
    pos_inail: str
    dipendente_codice: str
    dipendente_nome: str
    dipendente_cognome: str
    dipendente_cf: str
    mese: int
    anno: int
    data_nascita: str
    data_assunzione: str
    qualifica: str
    livello: str
    contratto_codice: str
    contratto_nome: str
    elementi_retributivi_totale: Decimal


@dataclass
class VoceCorpoBusta:
    assoggettamento: str | None  # A, B, C, or None
    codice_voce: str
    descrizione: str
    ore_gg_num_perc: Decimal | None = None
    dato_base: Decimal | None = None
    dato_figurativo: Decimal | None = None
    competenze: Decimal | None = None
    ritenute: Decimal | None = None


@dataclass
class FooterData:
    totale_ritenute: Decimal
    totale_competenze: Decimal
    arr_precedente: Decimal
    arr_attuale: Decimal
    netto_a_pagare: Decimal
    gg_detr: int | None = None
    fonte_azi: Decimal | None = None
    banca_ore: Decimal | None = None
    imp_fiscale: Decimal | None = None
    impos_lorda: Decimal | None = None
    detr_godute: Decimal | None = None
    impos_netta: Decimal | None = None
    fdo_tfr_ap: Decimal | None = None
    fdo_tfr_a: Decimal | None = None
    lordo_anno: Decimal | None = None
    inps_anno: Decimal | None = None
    iban: str | None = None


@dataclass
class ParsedPayslip:
    header: HeaderData
    voci: list[VoceCorpoBusta] = field(default_factory=list)
    footer: FooterData | None = None
    sha256: str = ""
    is_valid: bool = False
