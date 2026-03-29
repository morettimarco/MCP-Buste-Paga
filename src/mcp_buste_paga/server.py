"""MCP server for Italian INAZ payslip parsing and analysis."""

from __future__ import annotations

import glob
import json
import logging
import os
import pathlib

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from .db import (
    get_employee_info,
    get_payslip_details,
    get_salary_history,
    init_db,
    insert_busta,
    insert_voci,
    search_items,
    upsert_azienda,
    upsert_dipendente,
)
from .parser import parse_pdf

log = logging.getLogger(__name__)

DB_PATH = pathlib.Path.home() / ".mcp-buste-paga" / "buste_paga.db"
SKILL_PATH = pathlib.Path(__file__).parent.parent.parent / "skill.md"

mcp = FastMCP(
    name="mcp-buste-paga",
    instructions=(
        "Italian INAZ payslip (busta paga) parser and analyzer. "
        "Ingests PDF payslips into a local SQLite database and provides tools "
        "for salary analysis, tax breakdown, and payslip item search. "
        "All data is stored locally for privacy."
    ),
)


def _json(obj: dict | list | None) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@mcp.resource("buste-paga://interpretation-guide")
def interpretation_guide() -> str:
    """Full interpretation guide for reading and validating Italian INAZ payslip data.

    Covers all tools, field meanings, voce codes, IRPEF/INPS validation chains,
    conguaglio logic, and communication guidelines.
    """
    return _skill_text()


@mcp.prompt()
def use_interpretation_guide() -> list[TextContent]:
    """Load the full payslip interpretation guide into the conversation.

    Use this prompt before analysing payslip data to ensure correct
    interpretation of all fields, voce codes, and validation rules.
    """
    return [TextContent(type="text", text=_skill_text())]


@mcp.tool()
def ingest_payslips(directory_path: str) -> str:
    """Scan a directory for PDF payslips, parse them, and store in the database.

    Skips already-ingested files (SHA-256 deduplication).
    Returns a JSON summary: total files, ingested, duplicates skipped, failures.
    """
    conn = init_db(DB_PATH)
    pdf_files = sorted(glob.glob(os.path.join(directory_path, "**", "*.pdf"), recursive=True))

    results = {
        "total_files": len(pdf_files),
        "ingested": 0,
        "skipped_duplicate": 0,
        "failed": 0,
        "errors": [],
    }

    for pdf_path in pdf_files:
        fname = os.path.basename(pdf_path)
        try:
            payslip = parse_pdf(pdf_path)
            azienda_id = upsert_azienda(conn, payslip.header)
            dipendente_id = upsert_dipendente(conn, payslip.header, azienda_id)
            busta_id = insert_busta(conn, payslip, dipendente_id, fname)
            if busta_id is None:
                results["skipped_duplicate"] += 1
            else:
                insert_voci(conn, busta_id, payslip.voci)
                results["ingested"] += 1
                conn.commit()
        except Exception as e:
            results["failed"] += 1
            results["errors"].append(f"{fname}: {e}")
            log.exception("Failed to parse %s", fname)

    conn.close()
    return _json(results)


@mcp.tool()
def get_employee_summary() -> str:
    """Get employee and company information from the database.

    Returns the employee profile (name, fiscal code, hire date, role)
    and company details, plus the number of payslips stored.
    """
    conn = init_db(DB_PATH)
    info = get_employee_info(conn)
    conn.close()
    if not info:
        return _json({"error": "No employee data found. Run ingest_payslips first."})
    return _json(info)


@mcp.tool()
def get_salary_history_tool(year: int | None = None, limit: int = 12) -> str:
    """Get salary history ordered by most recent month first.

    Args:
        year: Filter to a specific year (optional).
        limit: Maximum number of months to return (default 12).

    Returns a JSON array with mese, anno, totale_competenze, totale_ritenute,
    netto_a_pagare, and lordo_anno for each month.
    """
    conn = init_db(DB_PATH)
    rows = get_salary_history(conn, year, limit)
    conn.close()
    return _json(rows)


@mcp.tool()
def get_payslip_details_tool(mese: int, anno: int) -> str:
    """Get the full details of a specific payslip by month and year.

    Args:
        mese: Month number (1-12).
        anno: Year (e.g. 2026).

    Returns the payslip master record plus all line items (voci) showing
    what was paid or deducted (base pay, overtime, taxes, etc.).
    """
    conn = init_db(DB_PATH)
    details = get_payslip_details(conn, mese, anno)
    conn.close()
    if not details:
        return _json({"error": f"No payslip found for {mese:02d}/{anno}"})
    return _json(details)


@mcp.tool()
def search_payslip_items(
    keyword: str,
    start_year: int | None = None,
    end_year: int | None = None,
) -> str:
    """Search payslip line items by description keyword.

    Searches the voci_corpo_busta table using SQL LIKE matching.
    Groups and sums results by month/year.

    Args:
        keyword: Search term (e.g. "Straordinario", "Ferie", "Ticket", "Commissioni").
        start_year: Filter from this year (optional).
        end_year: Filter up to this year (optional).

    Returns per-month breakdown and grand totals for competenze and ritenute.
    """
    conn = init_db(DB_PATH)
    result = search_items(conn, keyword, start_year, end_year)
    conn.close()
    return _json(result)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
