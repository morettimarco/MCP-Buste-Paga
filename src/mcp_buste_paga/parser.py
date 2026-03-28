"""PDF parser for Italian INAZ-format payslips.

Uses pdfplumber word-level extraction with X-coordinate column assignment
to reliably parse the tabular body section across multi-page payslips.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pdfplumber

from .models import FooterData, HeaderData, ParsedPayslip, VoceCorpoBusta
from .utils import compute_sha256, parse_italian_number, split_competenze_ritenute

log = logging.getLogger(__name__)

# ── Column X-coordinate boundaries (INAZ standard layout, 595pt width) ──────
# These are approximate midpoint ranges; we use right-edge (x1) for numbers
# since they are right-aligned. Detected dynamically when possible.
DEFAULT_COL_BOUNDARIES = {
    "assogg_x_max": 42,       # Assoggettamento flag (A/B/C) lives x0 < 42
    "codice_x_min": 42,       # Codice voce starts after assogg
    "codice_x_max": 78,       # Codice voce ends before descrizione
    "desc_x_min": 78,         # Descrizione starts
    "desc_x_max": 250,        # Descrizione ends
    "ore_x_min": 250,         # Ore/GG/Num/% column
    "ore_x_max": 320,
    "base_x_min": 320,        # Dato Base
    "base_x_max": 395,
    "fig_x_min": 395,         # Dato Figurativo
    "fig_x_max": 470,
    "comp_x_min": 470,        # Competenze/Ritenute (rightmost)
}

# Rows to skip (subtotals, informational)
SKIP_CODICI = {"V01"}  # Previdenziale non arrot. (informational base)
SUBTOTAL_CODICI = {"900"}  # Totale ritenute sociali


def parse_pdf(filepath: str | Path) -> ParsedPayslip:
    """Parse an INAZ payslip PDF and return a structured ParsedPayslip."""
    filepath = str(filepath)
    sha256 = compute_sha256(filepath)

    with pdfplumber.open(filepath) as pdf:
        if len(pdf.pages) < 1:
            raise ValueError("PDF has no pages")

        page0 = pdf.pages[0]

        # Parse header from page 0
        header = _parse_header(page0)

        # Detect column boundaries from header labels
        col_bounds = _detect_column_boundaries(page0)

        # Check if multi-page body (*** SEGUE *** on page 0)
        page0_text = page0.extract_text() or ""
        has_segue = "SEGUE" in page0_text

        if has_segue and len(pdf.pages) >= 3:
            # Multi-page: page 0 = body part 1, page 2 = body part 2 + footer
            voci_p0 = _extract_body_rows(page0, col_bounds, stop_at_segue=True)
            page2 = pdf.pages[2]
            voci_p2 = _extract_body_rows(page2, col_bounds, stop_at_segue=False)
            all_voci = voci_p0 + voci_p2
            footer = _parse_footer(page2)
        else:
            # Single-page: everything on page 0
            all_voci = _extract_body_rows(page0, col_bounds, stop_at_segue=False)
            footer = _parse_footer(page0)

    # Validate checksum
    is_valid = False
    if footer:
        calculated = (
            footer.totale_competenze
            - footer.totale_ritenute
            + footer.arr_attuale
            - footer.arr_precedente
        )
        diff = abs(calculated - footer.netto_a_pagare)
        is_valid = diff <= Decimal("1.00")
        if not is_valid:
            log.warning(
                "Checksum failed for %s/%s: calculated=%s, netto=%s, diff=%s",
                header.mese, header.anno, calculated, footer.netto_a_pagare, diff,
            )

    return ParsedPayslip(
        header=header,
        voci=all_voci,
        footer=footer,
        sha256=sha256,
        is_valid=is_valid,
    )


# ── Header parsing ───────────────────────────────────────────────────────────


def _parse_header(page: pdfplumber.page.Page) -> HeaderData:
    """Extract header fields using regex on full page text."""
    text = page.extract_text() or ""
    words = page.extract_words(x_tolerance=3, y_tolerance=3)

    # Company name: find on the line near top (may be prefixed by a numeric code)
    azienda_nome = ""
    azienda_cf = ""
    pos_inps = ""
    pos_inail = ""

    # Company CF — 11-digit number near "Cod.Fiscale"
    m = re.search(r"(\d{11})", text)
    if m:
        azienda_cf = m.group(1)

    # Company name — words on the line around Y=27 (between top labels and Codice row)
    company_words = [
        w for w in words
        if 22 < w["top"] < 35 and w["x0"] < 300
    ]
    if company_words:
        company_words.sort(key=lambda w: w["x0"])
        parts = [w["text"] for w in company_words]
        # First token might be a numeric company code
        if parts and parts[0].isdigit():
            parts = parts[1:]
        azienda_nome = " ".join(parts)

    # Pos.INPS — 10-digit number after company name on same line
    m = re.search(r"(\d{10})", text)
    if m:
        pos_inps = m.group(1)

    # Pos.INAIL
    m = re.search(r"(\d{8}-\d{2})", text)
    if m:
        pos_inail = m.group(1)

    # Employee code, name, surname
    dip_codice = ""
    dip_nome = ""
    dip_cognome = ""
    # Find the "COGNOME" label Y, then get data words on the next Y-row (~6pt below)
    cognome_label = next(
        (w for w in words if w["text"] == "COGNOME" and w["top"] < 80), None
    )
    if cognome_label:
        data_y = cognome_label["top"] + 6  # Data row is ~6pt below label
        emp_words = [
            w for w in words
            if abs(w["top"] - data_y) < 3 and w["x0"] > 28
        ]
        emp_words.sort(key=lambda w: w["x0"])
        for w in emp_words:
            if w["x0"] < 140 and w["text"].isdigit():
                dip_codice = w["text"]
            elif w["x0"] >= 140 and not dip_cognome:
                dip_cognome = w["text"]
            elif dip_cognome:
                dip_nome = (dip_nome + " " + w["text"]).strip()

    # Codice Fiscale dipendente
    dip_cf = ""
    m = re.search(r"([A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z])", text)
    if m:
        dip_cf = m.group(1)

    # Mese e Periodo Competenza — use word positions near MESE label
    mese = 0
    anno = 0
    mese_label = next(
        (w for w in words if w["text"] == "MESE" and w["top"] < 90), None
    )
    if mese_label:
        label_y = mese_label["top"]
        period_words = [
            w for w in words
            if abs(w["top"] - (label_y + 6)) < 3
            and 300 < w["x0"] < 400
            and w["text"].isdigit()
        ]
        period_words.sort(key=lambda w: w["x0"])
        if len(period_words) >= 2:
            mese = int(period_words[0]["text"])
            anno = int(period_words[1]["text"])

    # Data Nascita, Data Assunzione
    data_nascita = ""
    data_assunzione = ""
    date_words = [w for w in words if 100 < w["top"] < 115]
    date_words.sort(key=lambda w: w["x0"])
    date_pattern = re.compile(r"\d{2}-\d{2}-\d{4}")
    dates_found = [w["text"] for w in date_words if date_pattern.match(w["text"])]
    if len(dates_found) >= 1:
        data_nascita = dates_found[0]
    if len(dates_found) >= 2:
        data_assunzione = dates_found[1]

    # Qualifica, Livello, Contratto
    qualifica = ""
    livello = ""
    contratto_codice = ""
    contratto_nome = ""
    qual_words = [w for w in words if 120 < w["top"] < 135]
    qual_words.sort(key=lambda w: w["x0"])
    qual_texts = [w["text"] for w in qual_words]
    if qual_texts:
        qualifica = qual_texts[0] if qual_texts else ""
        livello = qual_texts[1] if len(qual_texts) > 1 else ""
        contratto_codice = qual_texts[2] if len(qual_texts) > 2 else ""
        contratto_nome = qual_texts[3] if len(qual_texts) > 3 else ""

    # Elementi Retributivi TOTALE
    totale = Decimal("0")
    totale_words = [w for w in words if 218 < w["top"] < 232]
    for w in totale_words:
        parsed = parse_italian_number(w["text"])
        if parsed and parsed > Decimal("100"):
            totale = parsed
            break

    return HeaderData(
        azienda_nome=azienda_nome,
        azienda_cf=azienda_cf,
        pos_inps=pos_inps,
        pos_inail=pos_inail,
        dipendente_codice=dip_codice,
        dipendente_nome=dip_nome,
        dipendente_cognome=dip_cognome,
        dipendente_cf=dip_cf,
        mese=mese,
        anno=anno,
        data_nascita=data_nascita,
        data_assunzione=data_assunzione,
        qualifica=qualifica,
        livello=livello,
        contratto_codice=contratto_codice,
        contratto_nome=contratto_nome,
        elementi_retributivi_totale=totale,
    )


# ── Column boundary detection ────────────────────────────────────────────────


def _detect_column_boundaries(page: pdfplumber.page.Page) -> dict:
    """Detect column boundaries from the header label row."""
    words = page.extract_words(x_tolerance=3, y_tolerance=3)

    # Find the header row containing "Voce" and "Descrizione"
    voce_word = None
    for w in words:
        if w["text"] == "Voce" and w["top"] > 200:
            voce_word = w
            break

    if not voce_word:
        return DEFAULT_COL_BOUNDARIES

    header_y = voce_word["top"]
    header_words = {
        w["text"]: w
        for w in words
        if abs(w["top"] - header_y) < 3
    }

    bounds = dict(DEFAULT_COL_BOUNDARIES)

    # Use header word positions to calibrate
    if "Voce" in header_words:
        v = header_words["Voce"]
        bounds["codice_x_min"] = v["x0"] - 12  # Account for assogg flag
        bounds["assogg_x_max"] = v["x0"] - 2

    if "Descrizione" in header_words:
        d = header_words["Descrizione"]
        bounds["codice_x_max"] = d["x0"] - 2
        bounds["desc_x_min"] = d["x0"] - 2

    if "Ore/Giorni/Num./%" in header_words:
        o = header_words["Ore/Giorni/Num./%"]
        bounds["desc_x_max"] = o["x0"] - 2
        bounds["ore_x_min"] = o["x0"] - 2
        bounds["ore_x_max"] = o["x1"] + 20

    if "Base" in header_words:
        b = header_words["Base"]
        bounds["base_x_min"] = b["x0"] - 20
        bounds["base_x_max"] = b["x1"] + 30

    if "Figurativo" in header_words:
        f = header_words["Figurativo"]
        bounds["fig_x_min"] = f["x0"] - 20
        bounds["fig_x_max"] = f["x1"] + 15

    if "Competenze/Ritenute" in header_words:
        c = header_words["Competenze/Ritenute"]
        bounds["comp_x_min"] = c["x0"] - 25

    return bounds


# ── Body row extraction ──────────────────────────────────────────────────────

# Pattern for codice voce: 2-5 digit number or letter+digits (I01, H06, J99)
CODICE_PATTERN = re.compile(r"^[A-Z]?\d{2,5}$")
# Pattern for Italian number (possibly with trailing minus)
NUMBER_PATTERN = re.compile(r"^[\d.,]+-?$")


def _extract_body_rows(
    page: pdfplumber.page.Page,
    col_bounds: dict,
    stop_at_segue: bool,
) -> list[VoceCorpoBusta]:
    """Extract body voce rows from a page using word-level X-coordinate mapping."""
    words = page.extract_words(x_tolerance=3, y_tolerance=3)

    # Find the header row Y (contains "Voce")
    header_y = None
    segue_y = None
    for w in words:
        if w["text"] == "Voce" and w["top"] > 200:
            header_y = w["top"]
        if w["text"] == "SEGUE":
            segue_y = w["top"]

    if header_y is None:
        return []

    # Determine body Y range
    body_y_start = header_y + 4  # Just below header row
    body_y_end = segue_y - 2 if (stop_at_segue and segue_y) else _find_footer_y(words)

    # Filter to body region words, excluding the rotated text on margins (x0 < 28)
    body_words = [
        w for w in words
        if body_y_start < w["top"] < body_y_end and w["x0"] > 28
    ]

    if not body_words:
        return []

    # Cluster words into rows by Y coordinate (within 4pt tolerance)
    rows = _cluster_by_y(body_words, tolerance=4.0)

    # Parse each row
    result = []
    for row_words in rows:
        voce = _parse_body_row(row_words, col_bounds)
        if voce:
            result.append(voce)

    return result


def _find_footer_y(words: list[dict]) -> float:
    """Find the Y coordinate where the footer starts (Ferie e Permessi row or totals)."""
    for w in words:
        if w["text"] == "Ferie" and w["top"] > 400:
            return w["top"] - 2
    # Fallback: look for "Totale" followed by "Ritenute"
    for w in words:
        if w["text"] == "Totale" and w["top"] > 400 and w["x0"] > 350:
            return w["top"] - 2
    return 800  # Full page height fallback


def _cluster_by_y(words: list[dict], tolerance: float) -> list[list[dict]]:
    """Group words into rows by Y coordinate proximity."""
    if not words:
        return []

    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    clusters: list[list[dict]] = []
    current_cluster = [sorted_words[0]]
    current_y = sorted_words[0]["top"]

    for w in sorted_words[1:]:
        if abs(w["top"] - current_y) <= tolerance:
            current_cluster.append(w)
        else:
            clusters.append(sorted(current_cluster, key=lambda w: w["x0"]))
            current_cluster = [w]
            current_y = w["top"]

    if current_cluster:
        clusters.append(sorted(current_cluster, key=lambda w: w["x0"]))

    return clusters


def _parse_body_row(
    row_words: list[dict], bounds: dict
) -> VoceCorpoBusta | None:
    """Parse a single body row from its constituent words."""
    if not row_words:
        return None

    assogg = None
    codice = None
    desc_parts: list[str] = []
    ore_parts: list[str] = []
    base_parts: list[str] = []
    fig_parts: list[str] = []
    comp_parts: list[str] = []

    for w in row_words:
        x0 = w["x0"]
        x1 = w["x1"]
        text = w["text"]
        mid_x = (x0 + x1) / 2

        if x0 < bounds["assogg_x_max"]:
            # Assoggettamento flag or possible codice if it looks like one
            if text in ("A", "B", "C"):
                assogg = text
            elif CODICE_PATTERN.match(text) and codice is None:
                codice = text
            elif text == "*":
                pass  # Header marker, ignore
            else:
                desc_parts.append(text)

        elif x0 < bounds["codice_x_max"]:
            if CODICE_PATTERN.match(text) and codice is None:
                codice = text
            else:
                desc_parts.append(text)

        elif mid_x < bounds["desc_x_max"]:
            desc_parts.append(text)

        elif mid_x < bounds["ore_x_max"]:
            ore_parts.append(text)

        elif mid_x < bounds["base_x_max"]:
            base_parts.append(text)

        elif mid_x < bounds["fig_x_max"]:
            fig_parts.append(text)

        else:  # Competenze/Ritenute
            comp_parts.append(text)

    if not codice:
        return None

    descrizione = " ".join(desc_parts).strip()
    if not descrizione:
        return None

    ore = parse_italian_number(" ".join(ore_parts)) if ore_parts else None
    base = parse_italian_number(" ".join(base_parts)) if base_parts else None
    fig = parse_italian_number(" ".join(fig_parts)) if fig_parts else None
    comp_raw = parse_italian_number(" ".join(comp_parts)) if comp_parts else None

    competenze, ritenute = split_competenze_ritenute(comp_raw)

    return VoceCorpoBusta(
        assoggettamento=assogg,
        codice_voce=codice,
        descrizione=descrizione,
        ore_gg_num_perc=ore,
        dato_base=base,
        dato_figurativo=fig,
        competenze=competenze,
        ritenute=ritenute,
    )


# ── Footer parsing ───────────────────────────────────────────────────────────


def _parse_footer(page: pdfplumber.page.Page) -> FooterData | None:
    """Parse footer section from page 2 using word-level extraction."""
    words = page.extract_words(x_tolerance=3, y_tolerance=3)
    text = page.extract_text() or ""

    # Find key footer values by their position
    # Totale Ritenute / Totale Competenze are on the same Y-line
    totale_rit = None
    totale_comp = None
    arr_prec = None
    arr_att = None
    netto = None

    # Find "Ferie e Permessi" row Y — totals are just below it
    ferie_y = None
    for w in words:
        if w["text"] == "Ferie" and w["top"] > 400:
            ferie_y = w["top"]
            break

    if ferie_y is None:
        return None

    # Totale Ritenute (x0 ~395-450) and Totale Competenze (x0 ~480-550)
    # are on the first number line after Ferie row
    totals_words = [
        w for w in words
        if ferie_y + 2 < w["top"] < ferie_y + 12
        and w["x0"] > 350
        and NUMBER_PATTERN.match(w["text"])
    ]
    totals_words.sort(key=lambda w: w["x0"])
    if len(totals_words) >= 2:
        totale_rit = parse_italian_number(totals_words[0]["text"])
        totale_comp = parse_italian_number(totals_words[1]["text"])
    elif len(totals_words) == 1:
        # Only one total found — determine which based on X position
        if totals_words[0]["x0"] > 470:
            totale_comp = parse_italian_number(totals_words[0]["text"])
        else:
            totale_rit = parse_italian_number(totals_words[0]["text"])

    # Arr. Precedente / Arr. Attuale — next number line after totals
    arr_words = [
        w for w in words
        if ferie_y + 12 < w["top"] < ferie_y + 22
        and w["x0"] > 350
        and NUMBER_PATTERN.match(w["text"])
    ]
    arr_words.sort(key=lambda w: w["x0"])
    if len(arr_words) >= 2:
        arr_prec = parse_italian_number(arr_words[0]["text"])
        arr_att = parse_italian_number(arr_words[1]["text"])

    # NETTO A PAGARE — look for large number near "PAGARE"
    netto_val = None
    # Search by regex on text
    m = re.search(r"Banca\s+ore\s+([\d.,]+)", text)
    banca_ore = parse_italian_number(m.group(1)) if m else None

    # NETTO is on the same line as "Banca ore" or nearby
    # Find it by looking for large number near x0 > 470 around the NETTO Y
    netto_y = None
    for w in words:
        if w["text"] == "PAGARE":
            netto_y = w["top"]
            break

    if netto_y:
        netto_words = [
            w for w in words
            if abs(w["top"] - netto_y) < 6
            and w["x0"] > 470
            and NUMBER_PATTERN.match(w["text"])
        ]
        if netto_words:
            # Pick the rightmost/largest number
            netto_words.sort(key=lambda w: w["x0"])
            netto_val = parse_italian_number(netto_words[-1]["text"])

    if totale_rit is None:
        totale_rit = Decimal("0")
    if totale_comp is None:
        totale_comp = Decimal("0")
    if arr_prec is None:
        arr_prec = Decimal("0")
    if arr_att is None:
        arr_att = Decimal("0")
    if netto_val is None:
        netto_val = banca_ore or Decimal("0")

    # GG Detr and Fonte Azi
    gg_detr = None
    fonte_azi = None

    # GG Detr — first number on the line with "Fonte Azi"
    fonte_y = None
    for w in words:
        if w["text"] == "Fonte" and w["top"] > 530:
            fonte_y = w["top"]
            break

    if fonte_y:
        # GG and Fonte are on the next Y-line after "Fonte Azi" label
        gg_fonte_words = [
            w for w in words
            if abs(w["top"] - (fonte_y + 9)) < 5
            and NUMBER_PATTERN.match(w["text"])
        ]
        gg_fonte_words.sort(key=lambda w: w["x0"])
        if len(gg_fonte_words) >= 1:
            gg_val = parse_italian_number(gg_fonte_words[0]["text"])
            if gg_val and gg_val < 400:  # GG is days in month
                gg_detr = int(gg_val)
        if len(gg_fonte_words) >= 2:
            fonte_azi = parse_italian_number(gg_fonte_words[1]["text"])

    # Dati Progressivi — dense line with multiple numbers
    imp_fiscale = None
    impos_lorda = None
    detr_godute = None
    impos_netta = None
    fdo_tfr_ap = None
    fdo_tfr_a = None
    lordo_anno = None
    inps_anno = None

    m = re.search(r"Imp\.fiscal", text)
    if m:
        # Find the data line (next line with numbers)
        prog_start = m.start()
        # Get position in text, find the numbers on next line
        remaining = text[prog_start:]
        lines = remaining.split("\n")
        if len(lines) >= 2:
            data_line = lines[1].strip()
            nums = re.findall(r"[\d.,]+-?", data_line)
            parsed_nums = [parse_italian_number(n) for n in nums]
            parsed_nums = [n for n in parsed_nums if n is not None]

            if len(parsed_nums) >= 1:
                imp_fiscale = parsed_nums[0]
            if len(parsed_nums) >= 2:
                impos_lorda = parsed_nums[1]
            if len(parsed_nums) >= 3:
                detr_godute = parsed_nums[2]
            if len(parsed_nums) >= 4:
                impos_netta = parsed_nums[3]
            if len(parsed_nums) >= 5:
                fdo_tfr_ap = parsed_nums[4]
            if len(parsed_nums) >= 6:
                fdo_tfr_a = parsed_nums[5]
            if len(parsed_nums) >= 7:
                lordo_anno = parsed_nums[6]
            if len(parsed_nums) >= 8:
                inps_anno = parsed_nums[7]

    # IBAN
    iban = None
    m = re.search(r"IBAN:\s*(IT\S+)", text)
    if m:
        iban = m.group(1)

    return FooterData(
        totale_ritenute=totale_rit,
        totale_competenze=totale_comp,
        arr_precedente=arr_prec,
        arr_attuale=arr_att,
        netto_a_pagare=netto_val,
        gg_detr=gg_detr,
        fonte_azi=fonte_azi,
        banca_ore=banca_ore,
        imp_fiscale=imp_fiscale,
        impos_lorda=impos_lorda,
        detr_godute=detr_godute,
        impos_netta=impos_netta,
        fdo_tfr_ap=fdo_tfr_ap,
        fdo_tfr_a=fdo_tfr_a,
        lordo_anno=lordo_anno,
        inps_anno=inps_anno,
        iban=iban,
    )
