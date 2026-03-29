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


# ── Voce codes reference resource ───────────────────────────────────────────

_VOCE_CODES = """\
# Buste Paga — Voce Code Reference

Compact lookup table: code → description → category → tax treatment.
"INPS base" = included in V01 contribution base.
"IRPEF base" = included in I01 taxable income.
"Figurative" = no cash effect, affects tax calculation only.

## Earnings (competenze, assoggettamento A)
| Code  | Description                   | INPS base | IRPEF base | Notes |
|-------|-------------------------------|-----------|------------|-------|
| 002   | Retribuzione ordinaria        | Yes       | Yes        | Base monthly salary = elementi_retributivi_totale |
| 008   | Festivita' (TFR)              | Yes       | Yes        | Public holiday pay; included in TFR base |
| 024   | E.D.R. Ente Bilaterale        | Yes       | Yes        | Fixed small monthly amount (~8.17€) |
| 040   | Bonus                         | Yes       | Yes        | Discretionary one-time bonus |
| 042   | Competenze varie              | Yes       | Yes        | Miscellaneous taxable earnings |
| 081   | 13ma mensilita'               | Yes       | Yes        | Mandatory Christmas bonus; IRPEF via I21 |
| 082   | 14ma mensilita'               | Yes       | Yes        | 14th month bonus (CCNL-dependent, June/July); IRPEF via I41, base in I02 |
| 086   | Ex-festiv. liquidate ore      | Yes       | Yes        | Monetized former-holiday hours; ore_gg_num_perc=hours, dato_base=hourly rate |
| 089   | R.o.l. liquidate ore          | Yes       | Yes        | Monetized expired ROL/leave hours; can be large (e.g. 68h × 45.71€) |
| 266   | Una tantum CCNL               | No        | Yes (I03)  | CCNL back-pay lump sum; taxed at prior-year rate via I48 |
| 314   | Indenn. Auto (tfr)            | Yes       | Yes        | Car allowance; included in TFR base |
| 359   | Commissioni (tfr)             | Yes       | Yes        | Sales commissions; variable; included in TFR base |
| 516   | Gym allowance                 | Depends   | Depends    | Wellness benefit; tax treatment depends on amount/policy |
| 758   | Compet. Nette varie           | No        | No         | Miscellaneous net items (expense reimbursements); bypasses INPS/IRPEF |
| 759   | Rimb.spese telef (netto)      | No        | No         | Phone reimbursement; tax-exempt, added directly to net |
| C75   | Rimborso IRPEF 730 dic.       | No        | No         | Annual 730 tax return refund processed via payslip; not new income |

## Leave tracking (no cash, no competenze/ritenute)
| Code  | Description                   | Notes |
|-------|-------------------------------|-------|
| 011   | Ferie godute gg               | Vacation days taken; ore_gg_num_perc=days; counter only |
| 012   | R.o.l./PE56 godute ore        | ROL/permit hours taken; counter only |
| 017   | Ex-festiv.godute (hh)         | Former holiday hours taken; counter only |
| 972   | GG Perm.Retribuito fig.       | Paid leave days (figurative); no cash effect |

## Figurative-only items (tax impact, no cash)
| Code  | Description                   | INPS base | IRPEF base | Notes |
|-------|-------------------------------|-----------|------------|-------|
| 655   | Polizza extra prof.           | No        | Yes        | Early code for accident insurance fringe benefit |
| 658   | Ass.Pol. Vita                 | Yes       | Yes        | Life insurance premium; taxable fringe benefit |
| 891   | StockO/ProfSh Fisc no net     | No        | Yes        | Stock options/profit sharing; CRITICAL — NOT in V01 but IS in I01; add dato_figurativo to IRPEF chain manually |
| 906   | Ticket elettronico            | No        | No         | Meal vouchers; tax-exempt up to legal threshold (8€/day electronic); ore_gg_num_perc=days, dato_base=per-day value |
| 961   | Ass.Pol.extra prof. imp.      | No        | Yes        | Accident insurance fringe benefit (intermediate code) |
| C27   | Tfr Maturato mese             | No        | No         | Monthly TFR accrual; informational only; actual handling via voce 293 |
| J99   | Ass.Pol.extra prof.autom.     | No        | Yes        | Accident insurance fringe benefit (current code) |

## Deductions (ritenute)
| Code  | Description                   | Notes |
|-------|-------------------------------|-------|
| 044   | Quadrifor dip.                | Quadrifor training fund; deducted January; in voce 900 but NOT deductible from IRPEF base — add back in IRPEF chain |
| 242   | Quas Dip Annuale              | QuAS health fund for Quadri; deducted January; same IRPEF treatment as 044 |
| 288   | Fonte Dip adesione            | One-time pension fund enrollment fee; very small (~3.62€) |
| 469   | ESPP deduction                | Post-tax employee stock purchase plan deduction |

## Social contributions (assoggettamento C)
| Code  | Rate    | Notes |
|-------|---------|-------|
| 150   | 9.19%   | Main INPS FPLD pension; dato_base × rate/100 ≈ ritenute |
| 162   | varies  | December conguaglio of supplementary pension contribution |
| 165   | 1%      | Additional pension contribution (income above threshold) |
| 51143 | 0.30%   | CIGS/FIS contribution within INPS annual ceiling |
| 51144 | 0.30%   | CIGS/FIS on income above ceiling; both 51143+51144 present = ceiling crossed |
| 51253 | 0.65%   | Historical FIS rate (2021-2022) |
| 51271 | 0.266%  | FIS contribution within INPS ceiling (current rate) |
| 51272 | 0.266%  | FIS on income above INPS ceiling |
| 762   | varies  | Tax-deductible pension fund contribution (DL47/2000); negative dato_figurativo reduces IRPEF base |
| 954   | varies  | Back-pay social contributions from prior year (CCNL renewals); may cause ~1€ INPS discrepancy |

## Pension fund items
| Code  | Description                   | Notes |
|-------|-------------------------------|-------|
| 280   | Fonte Dipendente              | Employee contribution to supplementary pension fund; rate in ore_gg_num_perc (e.g. 0.55%) |
| 293   | Smob.TFR Fonte                | TFR transfer to pension fund; ore_gg_num_perc=100 means full TFR redirected; negative dato_figurativo |

## Summary/informational rows (no cash, null competenze and ritenute)
| Code  | Description                   | What dato_base holds |
|-------|-------------------------------|----------------------|
| V01   | Previdenziale non arrot.      | Unrounded monthly INPS contribution base |
| 900   | Totale ritenute sociali       | Sum of all C-assoggettamento contributions this month |
| I01   | Impon. fiscale mese           | Monthly IRPEF taxable income (regular) |
| I02   | Impon. fiscale altra mens     | IRPEF taxable for 13ma/14ma bonus split |
| I03   | Impon. fiscale A.P.           | Prior-year back-pay IRPEF taxable (taxed at prior-year rate) |
| I10   | Contr. assist. massimale      | Health fund contribution ceiling (informational) |
| I11   | Imposta lorda                 | Monthly gross IRPEF; ore_gg_num_perc=bracket % |
| I20   | Riduzione impon. fiscale      | Taxable income reduction (e.g. DL47/2000 pension deduction) |
| I21   | Irpef cod.1001                | Monthly IRPEF payment on regular income (has ritenute) |
| I41   | Irpef cod.1001 altra mens     | IRPEF on 13ma/14ma bonus; ore_gg_num_perc=bracket % |
| I48   | Irpef cod.1002 A.P.           | IRPEF on prior-year back-pay at prior-year marginal rate |
| I36   | Detrazioni su oneri           | Tax deductions on documented expenses |
| H06   | Add.Reg.Comp. rata            | Regional surtax monthly installment; ore_gg_num_perc=installment number |
| H12   | Add.Reg.Agg. rata             | Additional regional surtax installment |
| H30   | Add.Com.Agg. rata             | Municipal surtax installment |
| H38   | Acconto Add.Com.Agg. Rata     | Municipal surtax advance installment |

## December-only conguaglio rows
| Code  | Description                   | Notes |
|-------|-------------------------------|-------|
| I59   | Imponibile addizionali        | Annual taxable base for regional/municipal surtaxes |
| I61   | Imponibile IRPEF annuo        | Total annual IRPEF taxable income |
| I64   | Imposta lorda annua           | Annual gross IRPEF; ore_gg_num_perc=top bracket % |
| I65   | Detrazioni spettanti          | Annual deductions; ore_gg_num_perc=days worked (365 if full year) |
| I68   | Irpef trattenuta azienda      | IRPEF already withheld Jan-Nov |
| I71   | Irpef cod.1001 cong. +        | Year-end IRPEF top-up (ritenute if positive = owe more; competenze if negative = refund) |

## IRPEF chain formula (monthly)
```
V01 (INPS base)
  − 900 (total social contributions)
  + 891.dato_figurativo (stock options — NOT in V01 but IS taxable)
  + 044.ritenute (Quadrifor — in 900 but NOT deductible for IRPEF)
  + 242.ritenute (QuAS — same as Quadrifor)
  − I03 (prior-year back-pay taxed separately)
  ≈ I01 + I02 (regular + bonus IRPEF taxable)
```
Tolerance: ±1€. If mismatch remains after all adjustments, flag as anomaly.
"""


@mcp.resource("buste-paga://voce-codes")
def voce_codes_reference() -> str:
    """Compact voce code reference table for Italian INAZ payslips.

    Lists all known codes with their description, category, INPS/IRPEF
    tax treatment, and key calculation notes. Use this for fast lookup
    during payslip analysis without loading the full interpretation guide.
    """
    return _VOCE_CODES


# ── Workflow prompts ─────────────────────────────────────────────────────────


@mcp.prompt()
def verify_payslip(mese: int, anno: int) -> list[TextContent]:
    """Step-by-step verification workflow for a specific payslip.

    Instructs the agent to fetch the payslip and run all 9 validation
    checks from the interpretation guide, showing the math for each.

    Args:
        mese: Month number (1-12).
        anno: Year (e.g. 2026).
    """
    return [TextContent(type="text", text=f"""\
Verify the payslip for {mese:02d}/{anno} by following these steps in order:

1. Call `get_payslip_details_tool(mese={mese}, anno={anno})` to fetch the full payslip.

2. Run ALL of the following checks. For each one, show the actual numbers and whether it passes or fails.

   **Check 1 — Monthly checksum (6.1)**
   totale_competenze − totale_ritenute + arr_attuale − arr_precedente ≈ netto_a_pagare (±0.05€)

   **Check 2 — Base salary consistency (6.2)**
   Report elementi_retributivi_totale and flag if it changed from the previous month
   (call `get_salary_history_tool(limit=3)` to compare, if needed).

   **Check 3 — INPS validation (6.3)**
   For voce 150: dato_base × (ore_gg_num_perc / 100) ≈ ritenute (±0.02€)
   If voces 51143+51144 both appear, verify each independently and note the ceiling split.

   **Check 4 — IRPEF chain (6.4)**
   Apply the full formula:
     V01.dato_base − 900.dato_base + 891.dato_figurativo (if present) + 044.ritenute (if present) + 242.ritenute (if present) − I03.dato_base (if present) ≈ I01.dato_base + I02.dato_base (if present)
   Tolerance ±1€. If a mismatch remains after all adjustments, flag it.

   **Check 5 — Figurative items (4.3)**
   List all voci where competenze and ritenute are both null but dato_figurativo is set.
   Confirm they do NOT affect netto_a_pagare.

   **Check 6 — is_valid flag**
   Report the is_valid value from the master record and confirm it matches Check 1.

   **Check 7 — Addizionali installments (6.8)**
   If H06/H12/H30/H38 are present, report the installment number (ore_gg_num_perc) and amount.

   **Check 8 — TFR (6.7)**
   If voce C27 is present, confirm dato_figurativo ≈ totale_competenze / 13.5.
   Report whether voce 293 (TFR to pension fund) is present.

   **Check 9 — December conguaglio (6.6) — only if anno={anno}, mese=12**
   If this is December: verify I64.dato_base − I65.dato_base − I68.dato_base ≈ I71.ritenute.

3. Summarise: how many checks passed, how many failed, and any anomalies detected.
   Show all calculations explicitly. Do not guess — if a voce is absent, say so.
""")]


@mcp.prompt()
def analyze_annual_salary(anno: int) -> list[TextContent]:
    """Annual salary analysis workflow for a given year.

    Instructs the agent to fetch and correctly interpret a full year
    of salary data, using December's conguaglio for definitive annual figures.

    Args:
        anno: Year to analyse (e.g. 2025).
    """
    return [TextContent(type="text", text=f"""\
Analyse the full salary picture for {anno} using this exact sequence:

1. Call `get_salary_history_tool(year={anno}, limit=12)` to get all months.

2. For each month, report:
   - Month, netto_a_pagare (net take-home), totale_competenze (gross earnings), totale_ritenute (deductions)
   - Flag any month where is_valid = 0

3. Identify variable pay months:
   - Where totale_competenze is significantly higher than elementi_retributivi_totale
   - Explain that the excess is variable pay (commissions, bonuses, overtime)
   - Note that higher-income months attract a higher IRPEF marginal rate

4. **Annual gross — use December only**:
   Call `get_payslip_details_tool(mese=12, anno={anno})`.
   - Use voce I61.dato_base as the authoritative annual IRPEF taxable income
   - Use voce I64.dato_base as the annual gross IRPEF
   - Use voce I65.dato_base as the annual deductions
   - Report lordo_anno from the master record only as a cross-check; if it differs from I61, prefer I61
   - Do NOT use lordo_anno from non-December months (unreliable due to parser positional overlap)

5. Conguaglio analysis (December):
   - Report I64 − I65 = net annual IRPEF due
   - Report I68 = IRPEF already withheld Jan-Nov
   - Report I71 ritenute = year-end adjustment (positive = extra tax; negative = refund)
   - Explain why the conguaglio occurred (e.g. commission spike pushed income into 43% bracket)

6. Summary:
   - Total net pay for the year (sum of all netto_a_pagare)
   - Annual gross (from I61)
   - Effective tax rate = total IRPEF withheld / annual IRPEF taxable income
   - Biggest earning month and smallest net month, with explanation
""")]


@mcp.prompt()
def compare_months(mese1: int, anno1: int, mese2: int, anno2: int) -> list[TextContent]:
    """Side-by-side comparison of two payslip months.

    Instructs the agent to normalise for one-off items before comparing,
    so structural salary changes are not confused with variable pay.

    Args:
        mese1: First month (1-12).
        anno1: First year.
        mese2: Second month (1-12).
        anno2: Second year.
    """
    return [TextContent(type="text", text=f"""\
Compare the payslips for {mese1:02d}/{anno1} and {mese2:02d}/{anno2}:

1. Fetch both payslips:
   - `get_payslip_details_tool(mese={mese1}, anno={anno1})`
   - `get_payslip_details_tool(mese={mese2}, anno={anno2})`

2. Build a side-by-side comparison table for these master record fields:
   elementi_retributivi_totale | totale_competenze | totale_ritenute | netto_a_pagare

3. **Normalise before drawing conclusions** — identify and strip out one-off items in each month:
   - 13ma mensilita' (voce 081) — December only
   - 14ma mensilita' (voce 082) — June/July only
   - Una tantum CCNL back-pay (voce 266)
   - Commissioni (voce 359) — compare the amounts, don't treat as structural
   - Bonus (voce 040)
   - Conguaglio IRPEF (voce I71) — December only
   - Annual fund contributions: Quadrifor (044), QuAS (242) — January only
   - Monetised leave: ROL liquidate (089), ex-festiv. liquidate (086)
   - Stock options (voce 891)
   - Any IRPEF refund (voce C75)

4. After stripping one-offs, report:
   - Normalised competenze for each month (base salary + regular recurring items only)
   - Whether elementi_retributivi_totale changed (structural salary difference)
   - Whether the IRPEF burden per euro of normalised gross changed (bracket shift)

5. Summarise the key differences and their likely causes.
   Do not attribute differences to "variable pay" without identifying the specific voce.
""")]


@mcp.prompt()
def search_and_summarize(keyword: str) -> list[TextContent]:
    """Search for a payslip item and produce a structured annual summary.

    Instructs the agent to call search_payslip_items and present results
    grouped by year with totals and absence flags.

    Args:
        keyword: Search term (e.g. "Commissioni", "Straordinario", "Ticket").
    """
    return [TextContent(type="text", text=f"""\
Search for "{keyword}" across all payslips and summarise the findings:

1. Call `search_payslip_items(keyword="{keyword}")` to get all matching line items.

2. If the result is empty:
   - Report no matches found
   - Suggest alternative keywords from the search keywords table in the interpretation guide

3. If results are found:

   a. **Group by year**: For each calendar year present in the results, report:
      - Total competenze for that year
      - Total ritenute for that year
      - Number of months where the item appeared
      - Average monthly amount (total / months present)

   b. **Flag absent months**: For each year where the item appeared in SOME months but not all 12,
      call `get_salary_history_tool(year=<year>)` to confirm which months exist in the database,
      then report which months are missing this item (e.g. "absent in Jan, Feb 2025").

   c. **Trend**: State whether amounts are increasing, decreasing, or stable year-over-year.

   d. **Figurative items**: If tot_competenze and tot_ritenute are both null for the results,
      note that this is a figurative item with no cash effect, and report the dato_figurativo
      totals from the detailed payslip view instead.

4. Grand totals: report grand_total_competenze and grand_total_ritenute from the search result.

5. If the keyword matches a voce with known semantics (e.g. "Commissioni", "Ticket", "FPLD"),
   briefly explain what it represents using the voce code reference.
""")]


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
