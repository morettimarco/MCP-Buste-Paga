---
name: BustePagaMCPSKILL
description: Use this skill to properly use the buste-paga MCP server and enable the agent to interpret and validate the data that is retrived form the methods get_employee_summary get_salary_history_tool get_payslip_details_tool search_payslip_items
---

# Buste Paga MCP Server — Interpretation Skill

You are connected to the **mcp-buste-paga** MCP server, which stores parsed Italian payslip data (INAZ format) in a local SQLite database. This skill teaches you how to use each tool, interpret every field accurately, validate calculations, and detect anomalies.

> **Golden rule**: Never guess. If a field is `null`, say so. If a calculation doesn't balance, flag it. Payslip data is legally and financially sensitive — accuracy is non-negotiable.

---

## 1. Available Tools

| Tool | Purpose |
|---|---|
| `ingest_payslips(directory_path)` | Scan a folder recursively for PDF payslips, parse them, and store in the database. Deduplication via SHA-256. |
| `get_employee_summary()` | Employee profile + company info + payslip count. |
| `get_salary_history_tool(year?, limit?)` | Monthly salary history: gross, net, deductions. Most recent first. |
| `get_payslip_details_tool(mese, anno)` | Full payslip for a specific month: master record + all line items (voci). |
| `search_payslip_items(keyword, start_year?, end_year?)` | Search line items by description keyword across all payslips, grouped by month. |

---

## 2. Interpreting `get_employee_summary()`

Returns a single JSON object. Field-by-field meaning:

| Field | Meaning | Notes |
|---|---|---|
| `codice` | Internal employee code | Company-assigned ID number |
| `nome`, `cognome` | First name, Last name | |
| `codice_fiscale` | Italian fiscal code (tax ID) | 16-character alphanumeric, unique to the person. Encodes birth date, place, and gender. |
| `data_nascita` | Date of birth | Format: `DD-MM-YYYY` |
| `data_assunzione` | Hire date | **Critical**: determines seniority for scatti di anzianita (seniority raises), TFR accrual, notice period. |
| `qualifica` | Professional qualification | e.g. "Quadro" (middle manager), "Impiegato" (white collar), "Operaio" (blue collar), "Dirigente" (executive). |
| `livello` | Contract level | e.g. "Q", "1", "2", etc. Determines minimum salary per CCNL. |
| `contratto_codice` | CCNL code | National collective agreement code (e.g. "009" = Commercio/Trade sector). |
| `contratto_nome` | CCNL name | e.g. "Commercio", "Metalmeccanico", "Credito". Determines all pay rules. |
| `azienda_nome` | Company legal name | |
| `azienda_cf` | Company fiscal code / P.IVA | |
| `pos_inps` | INPS registration number | The company's social security "account". Identifies the sector and contribution rates. |
| `pos_inail` | INAIL registration number | Workplace injury insurance registration. |
| `num_buste` | Total payslips in database | |
| `first_period`, `last_period` | Earliest and latest payslip period | Format: `MM/YYYY` |

### How to use this data
- **Seniority**: Calculate from `data_assunzione` to the current date to determine tenure-based entitlements.
- **Qualifica + Livello**: Together they identify the employee's exact position in the CCNL pay scale. "Quadro" is a special category between Impiegato and Dirigente with enhanced benefits (e.g. supplementary health insurance, extra notice period).
- **CCNL**: All salary rules (minimum pay, overtime rates, leave entitlements, seniority raise schedule) derive from this contract.

---

## 3. Interpreting `get_salary_history_tool()`

Returns a JSON array of monthly summaries ordered by most recent first.

| Field | Meaning | Notes |
|---|---|---|
| `mese` | Month (1-12) | |
| `anno` | Year | |
| `elementi_retributivi_totale` | Monthly base gross salary | Sum of all fixed pay elements in the header (Minimo Tabellare + Contingenza + Superminimo + Scatti, etc.). This is the "theoretical" salary before any variable items. If this changes between months, the employee got a raise or a contractual renewal happened. |
| `totale_competenze` | Total earnings (competenze) | Sum of all positive items in the body. Includes base pay, commissions, bonuses, 13th month, holiday pay. **This is NOT take-home pay.** |
| `totale_ritenute` | Total deductions (ritenute) | Sum of all negative items: INPS contributions, IRPEF tax, addizionali, ESPP, pension fund contributions, etc. |
| `netto_a_pagare` | Net pay (take-home) | The actual bank transfer amount. **Formula**: `totale_competenze - totale_ritenute + rounding_adjustments` (tolerance ±0.05€). |
| `lordo_anno` | Year-to-date gross (annual) | **Only reliable in December** (conguaglio month). In non-December months this field may show `null` or may contain an incorrect value due to parser positional overlap with `fdo_tfr_a` (compare the two — if they are nearly identical, `lordo_anno` is unreliable). Only use the December value for annual gross analysis. |
| `is_valid` | Checksum validation | 1 = `totale_competenze - totale_ritenute + arr_attuale - arr_precedente ≈ netto_a_pagare` (±0.05€). 0 = checksum failed, data may need manual review. See section 6.1 for the full formula. |

### Key analysis patterns

**Spotting raises**: Compare `elementi_retributivi_totale` month-over-month. A change indicates a salary adjustment (contractual renewal, promotion, seniority raise).

**Identifying variable pay months**: When `totale_competenze` is significantly higher than `elementi_retributivi_totale`, the difference is variable pay (commissions, bonuses, overtime, back-pay).

**December anomalies**: December often shows unusual figures because it includes:
- 13ma mensilita (13th month salary — mandatory Christmas bonus equal to one month's base pay)
- Year-end tax reconciliation (conguaglio fiscale) which can make deductions spike or drop
- Possibly 14ma mensilita in some CCNLs

**Net pay volatility**: Large swings in `netto_a_pagare` are normal when commissions or bonuses are paid. The tax system is progressive — higher months get taxed at a higher marginal rate.

---

## 4. Interpreting `get_payslip_details_tool()`

Returns the full payslip: master record + `voci` array (line items). This is the most complex and information-rich tool.

### 4.1 Master record fields

| Field | Meaning | Notes |
|---|---|---|
| `dipendente_id` | FK to employee | |
| `mese`, `anno` | Period | |
| `elementi_retributivi_totale` | Base gross salary | Same as in salary history |
| `totale_competenze` | Total earnings | |
| `totale_ritenute` | Total deductions | |
| `arr_precedente` | Previous rounding adjustment | Carried from last month's rounding |
| `arr_attuale` | Current rounding adjustment | Applied this month to round `netto_a_pagare` to whole euros |
| `netto_a_pagare` | Net pay | Always a round number (whole euros) due to `arr_attuale` |
| `gg_detr` | Days for tax deductions | Number of days used to calculate work-related tax deductions (detrazioni lavoro dipendente). Usually 28-31 for normal months. **365 in December** = year-end recalculation of annual deductions. |
| `fonte_azi` | Company pension fund contribution | Employer's contribution to the supplementary pension fund (e.g. Fondo Fonte for Commercio CCNL). Not deducted from the employee — this is extra. |
| `banca_ore` | Hours bank balance | **CAUTION**: In the current parser, this field may contain the same value as `netto_a_pagare` due to positional overlap in the PDF layout. Do NOT use this field for hours bank analysis — cross-reference with the actual "Banca Ore" line items in voci instead. |
| `imp_fiscale` | Year-to-date taxable income (IRPEF) | Progressive cumulative total. In month N, this should equal the sum of all monthly IRPEF taxable amounts from January to month N. |
| `impos_lorda` | Gross tax (IRPEF lorda) | Year-to-date gross IRPEF. **CAUTION**: Due to positional overlap in the footer parsing, this field may not always be accurately captured. Cross-reference with voce `I11` (monthly) or `I64` (December annual) for reliable values. |
| `detr_godute` | Tax deductions applied (detrazioni) | Year-to-date tax deductions. **CAUTION**: Same positional parsing caveat as `impos_lorda`. Cross-reference with voce `I36`/`I65` for reliable values. |
| `impos_netta` | Net IRPEF due | The actual net IRPEF amount. Due to footer parsing complexity, do NOT assume `impos_netta = impos_lorda - detr_godute` from the master record fields — instead, validate via the voce-level tax chain (see section 6.4). In general, net IRPEF = gross IRPEF - deductions, capped at 0 (tax incapience). |
| `fdo_tfr_ap` | TFR fund — previous year balance | Severance pay (Trattamento di Fine Rapporto) accrued up to the end of the previous year. |
| `fdo_tfr_a` | TFR fund — current year accrual | Year-to-date TFR accrued in the current year. Monthly accrual ≈ gross pay / 13.5. If TFR is sent to a supplementary pension fund, this may reset monthly. |
| `lordo_anno` | Annual gross pay | Only populated in December. |
| `inps_anno` | Annual INPS contributions | Only populated in December (if at all). |
| `iban` | Bank account for payment | |
| `pdf_sha256` | File hash for deduplication | |
| `is_valid` | Checksum result | |

### 4.2 Line items (`voci`) — The heart of the payslip

Each voce has:

| Field | Meaning |
|---|---|
| `assoggettamento` | Tax/contribution subjection code: **"A"** = subject to both tax and social contributions (most pay items), **"C"** = social contribution line (the INPS deduction itself), **null** = informational/summary row or tax-exempt item |
| `codice_voce` | Numeric or alphanumeric code from the payroll software (INAZ). Not standardized across companies. |
| `descrizione` | Human-readable description of the item |
| `ore_gg_num_perc` | Quantity: hours, days, count, or percentage — depends on context |
| `dato_base` | Base value: hourly/daily rate, or the tax base for contribution calculations |
| `dato_figurativo` | Notional/figurative value. Affects tax calculation but does NOT produce a cash payment. See section 4.3. |
| `competenze` | Earnings amount (positive = money TO the employee) |
| `ritenute` | Deductions amount (positive number = money FROM the employee) |

### 4.3 Understanding `dato_figurativo` (Figurative amounts)

Figurative amounts are **non-cash values** that appear in the payslip for tax/contribution purposes only. They inflate or deflate the taxable base WITHOUT affecting actual cash flow.

Examples from real data:
- **`Ass.Pol. Vita` (Life insurance policy)**: `dato_figurativo = 37.50` — The company pays a life insurance premium on behalf of the employee. The employee doesn't receive cash, but the value is taxed as a fringe benefit.
- **`Ass.Pol.extra prof.autom.` (Extra-professional accident policy)**: Same logic — taxable benefit, no cash.
- **`Ticket elettronico` (Meal vouchers)**: `dato_figurativo = 160.00`, `ore_gg_num_perc = 20`, `dato_base = 8.00` — 20 working days × 8€ per voucher = 160€ in meal vouchers. These are tax-exempt up to the legal threshold (currently 8€/day for electronic vouchers). They appear as figurative because they're not salary.
- **`Smob.TFR Fonte`**: `dato_figurativo = -1559.46` — TFR being transferred to the pension fund. Negative figurative because it's being redirected, not paid as cash.
- **`Ctr.fis.ded.DL47/00 c/dip`**: `dato_figurativo = -474.17` — Tax-deductible pension fund contribution. Negative because it reduces the IRPEF taxable base.

**Rule**: If a voce has only `dato_figurativo` and both `competenze` and `ritenute` are null, it does NOT affect `netto_a_pagare`. It only affects the tax calculation.

### 4.4 Voce classification guide

#### Earnings (competenze)

| Code | Description | Meaning |
|---|---|---|
| `002` | Retribuzione ordinaria | Base monthly salary. Corresponds to `elementi_retributivi_totale`. |
| `008` | Festivita' (TFR) | Holiday pay for public holidays falling on working days. "(TFR)" means it's included in the TFR calculation base. |
| `024` | E.D.R. Ente Bilaterale | "Elemento Distinto della Retribuzione" — a fixed monthly amount (historically from inflation indexing) plus bilateral body contribution. Typically small (e.g. 8.17€). |
| `081` | 13ma mensilita' | 13th month salary (Christmas bonus). Legally mandatory. Appears in December. `ore_gg_num_perc = 12` means 12/12ths accrued. IRPEF on this bonus appears in the normal I21 voce. |
| `082` | 14ma mensilita' | 14th month salary. Appears in June/July depending on CCNL. `ore_gg_num_perc = 12` means 12/12ths. **Important**: The 14ma is taxed separately — its IRPEF shows in `I41` (not I21), and its taxable base is in `I02` (not I01). See section 6.4. |
| `266` | Una tantum CCNL | One-time back-pay from a CCNL contract renewal. When national negotiations retroactively increase minimum pay, the difference for past months is paid as a lump sum. Triggers separate back-pay tax lanes (I03/I48). |
| `314` | Indenn. Auto (tfr) | Car allowance. "(tfr)" means included in TFR base. |
| `359` | Commissioni (tfr) | Sales commissions. Variable pay — can fluctuate heavily month to month. "(tfr)" = included in TFR base. |
| `040` | Bonus | One-time discretionary bonus payment. Taxable (assoggettamento A). |
| `042` | Competenze varie | Miscellaneous taxable earnings (assoggettamento A). Generic catch-all for items that don't have a specific voce code. |
| `516` | Gym allowance | Company wellness/gym reimbursement. Treated as a welfare benefit — may or may not be taxable depending on amount and company policy. |
| `758` | Compet. Nette varie | Miscellaneous net earnings. No assoggettamento — these bypass INPS and IRPEF. Added directly to net pay (e.g. expense reimbursements processed as net items). |
| `759` | Rimb.spese telef (netto) | Telephone expense reimbursement (net). Tax-exempt — added directly to net pay without affecting INPS/IRPEF. |
| `C75` | Rimborso IRPEF 730 dic. | IRPEF refund from the annual 730 tax return, processed through the employer's payslip. No assoggettamento — this is a tax credit refund, not new income. Appears as competenze but does NOT increase taxable income. |

#### Tracking-only items (no cash effect, no competenze/ritenute)

| Code | Description | Meaning |
|---|---|---|
| `011` | Ferie godute gg | Vacation days taken in the month. `ore_gg_num_perc` = number of days. Used to update the vacation balance counter only. |
| `012` | R.o.l./PE56 godute ore | ROL (Riduzione Orario di Lavoro) or PE56 permit hours taken. `ore_gg_num_perc` = hours. Tracking only — updates the leave balance counter. |
| `017` | Ex-festiv.godute (hh) | Former public holiday hours taken (ex-festivita permessi). `ore_gg_num_perc` = hours. Tracking only. |
| `086` | Ex-festiv. liquidate ore | Former holiday hours **monetized** (paid out). `ore_gg_num_perc` = hours, `dato_base` = hourly rate. Has competenze (assoggettamento A). Occurs when unused ex-festivita hours expire and are forcibly paid out. |
| `089` | R.o.l. liquidate ore | ROL/leave hours **monetized** (paid out). `ore_gg_num_perc` = hours, `dato_base` = hourly rate. Has competenze (assoggettamento A). Occurs when unused ROL hours expire per CCNL rules and are forcibly paid out — typically in July. Can be a significant amount (e.g. 68 hours × 45.71€/hr = 3108.57€). |
| `972` | GG Perm.Retribuito fig. | Figurative paid leave days (permesso retribuito). `ore_gg_num_perc` = days. Tracking only — no cash effect. Records that the employee was on paid leave. |

#### Figurative-only items (tax impact, no cash)

| Code | Description | Meaning |
|---|---|---|
| `658` | Ass.Pol. Vita | Company-paid life insurance premium. `dato_figurativo` = premium amount. Taxable fringe benefit (increases IRPEF base) but no cash to employee. Included in INPS base (V01). |
| `J99` | Ass.Pol.extra prof.autom. | Company-paid extra-professional accident insurance. `dato_figurativo` = premium. Taxable fringe benefit. |
| `891` | StockO/ProfSh Fisc no net | Stock options or profit sharing — taxable income. `dato_figurativo` = taxable value. **Critical**: This amount is subject to IRPEF but NOT included in the INPS base (V01). It is added directly to the IRPEF taxable base (I01). Large stock option vesting events can significantly increase the month's tax burden without any corresponding cash payment. |
| `906` | Ticket elettronico | Electronic meal vouchers. `ore_gg_num_perc` = working days, `dato_base` = per-day value, `dato_figurativo` = total value. Tax-exempt up to the legal threshold for electronic vouchers. **Note**: the per-day value has changed over time (7€/day in 2023, 8€/day from 2024). Not in INPS or IRPEF base. |
| `655` | Polizza extra prof. | Extra-professional accident insurance policy (earliest code variant, used before 961 and J99). Same meaning — taxable fringe benefit. |
| `961` | Ass.Pol.extra prof. imp. | Extra-professional accident insurance (intermediate code, replaced by J99 in later payslips). Same meaning — taxable fringe benefit. |
| `C27` | Tfr Maturato mese | Monthly TFR accrual. `dato_figurativo` = the TFR amount accrued this month (approximately gross pay / 13.5). Informational only — the actual TFR handling is in voce 293 (transfer to pension fund). |

#### Deductions (ritenute)

| Code | Description | Meaning |
|---|---|---|
| `288` | Fonte Dip adesione | One-time pension fund enrollment fee (Fondo Fonte). Charged when the employee first joins the supplementary pension fund. Very small amount (e.g. 3.62€). |
| `469` | ESPP deduction | Employee Stock Purchase Plan deduction. Post-tax deduction for purchasing company shares at a discount. |
| `044` | Quadrifor dip. | Quadrifor fund contribution — training fund for Quadri (middle managers) in the Commercio sector. Annual fee, usually deducted in January. **Note**: Included in voce 900 (Totale ritenute sociali) but NOT deductible from the IRPEF base — this means the employee still pays income tax on this amount. |
| `242` | Quas Dip Annuale | QuAS health fund annual contribution for Quadri. Deducted once a year (January). Same IRPEF treatment as Quadrifor. |

#### Social contributions (assoggettamento = "C")

| Code | Description | Meaning |
|---|---|---|
| `150` | Contributo FPLD con max | Main INPS pension contribution. `ore_gg_num_perc` = rate (9.19%). `dato_base` = contribution base (rounded). Formula: `dato_base × ore_gg_num_perc / 100 = ritenute`. |
| `162` | Cong.FAP Aggiuntivo max | Year-end reconciliation of supplementary pension contribution (appears in December). |
| `165` | Contrib FAP Aggiunt mass. | Additional pension contribution for income above a threshold (contributo aggiuntivo). Rate = 1%. |
| `51143` | CtrCIGS max FIS >15dip | CIGS contribution to FIS fund, on income up to the INPS ceiling (massimale). Rate = 0.30%. |
| `51144` | CtrCIGS ol.max FIS > 15 | CIGS contribution on income ABOVE the INPS ceiling. Same rate (0.30%) but on the overshoot. When both `51143` and `51144` appear in the same month, it means the employee's monthly income crossed the INPS contribution ceiling — the base was split between "within ceiling" and "above ceiling" portions. |
| `51271` | F.I.S. con max > 15 dip. | FIS (Fondo di Integrazione Salariale) contribution on income within the INPS ceiling. Rate = 0.266%. |
| `51253` | F.do Integr. Salar. 0,65% | Historical FIS (Fondo di Integrazione Salariale) contribution at the old 0.65% rate. Used in 2021-2022 before the rate structure changed to the current 51271/51272 split. |
| `51272` | F.I.S.oltre max > 15 dip. | FIS contribution on income above the INPS ceiling. Same split logic as CIGS (51143/51144). |
| `762` | Ctr.fis.ded.DL47/00 c/dip | Tax-deductible pension fund contribution (DL 47/2000). Appears as negative `dato_figurativo` — reduces IRPEF taxable base. |
| `954` | Contr. Arretrati A.P. | Back-pay social contributions from a previous year (triggered by CCNL renewals or corrections). `ore_gg_num_perc` = the rate applied, `dato_base` = the back-pay base, `dato_figurativo` = the contribution amount (negative = adjustment). May cause ~1€ discrepancies in INPS validation (check 6.3). |

#### Pension fund items

| Code | Description | Meaning |
|---|---|---|
| `280` | Fonte Dipendente | Employee's voluntary contribution to supplementary pension fund (Fondo Fonte for Commercio). Rate from `ore_gg_num_perc` (e.g. 0.55%). Base from `dato_base`. |
| `293` | Smob.TFR Fonte | TFR transfer to pension fund. `ore_gg_num_perc = 100` means 100% of TFR goes to the fund. `dato_figurativo` is negative (leaves the company, goes to the fund). |

#### Summary/informational rows (no cash effect)

These rows have both `competenze` and `ritenute` as null. They provide calculated intermediate values:

| Code | Description | What `dato_base` represents |
|---|---|---|
| `V01` | Previdenziale non arrot. | Unrounded INPS contribution base for the month |
| `900` | Totale ritenute sociali | Total social contributions for the month (sum of all "C" assoggettamento items) |
| `I01` | Impon. fiscale mese | Monthly IRPEF taxable income. See section 6.4 for the full computation (base − social contributions + adjustments). |
| `I10` | Contr. assist. massimale | Health fund contribution ceiling (informational). |
| `I11` | Imposta lorda | Monthly gross IRPEF. `ore_gg_num_perc` = marginal tax bracket % (e.g. 23%, 35%, 43%). `dato_base` = the calculated tax amount. |
| `I02` | Impon. fiscale altra mens | IRPEF taxable for an additional month bonus (13ma/14ma). IRPEF base split: I01 for regular income, I02 for bonus. `I01 + I02 ≈ total IRPEF-taxable`. |
| `I03` | Impon. fiscale A.P. | Previous-year IRPEF taxable income (from back-pay like CCNL Una tantum). Taxed at the **prior year's** marginal rate. Subtracted from the regular IRPEF chain (see 6.4). |
| `I20` | Riduzione impon. fiscale | Taxable income reduction applied before IRPEF calculation (e.g. from tax-deductible pension fund contributions via DL 47/2000). `dato_base` = amount of reduction. |
| `I21` | Irpef cod.1001 | Monthly IRPEF payment (tax code 1001) on regular income. This IS a real deduction — check `ritenute`. |
| `I41` | Irpef cod.1001 altra mens | IRPEF on the additional month bonus (13ma/14ma). Separate from I21. `ore_gg_num_perc` = tax bracket %. |
| `I48` | Irpef cod.1002 A.P. | IRPEF on previous-year back-pay. `ore_gg_num_perc` = prior year's marginal rate. |
| `I36` | Detrazioni su oneri | Tax deductions on documented expenses (oneri deducibili) |
| `H01` | Add.Reg.Comp. dovuta | Regional surtax calculated for the year (total due) |
| `H04` | Add.Reg.Comp. da tratt. | Regional surtax remaining to be withheld |
| `H06` | Add.Reg.Comp. rata | Regional surtax monthly installment. `ore_gg_num_perc` = installment number. |
| `H07` | Add.Reg.Agg. dovuta | Additional regional surtax calculated |
| `H10` | Add.Reg.Agg. da tratt. | Additional regional surtax remaining |
| `H12` | Add.Reg.Agg. rata | Additional regional surtax installment |
| `H25` | Add.Com.Agg. dovuta | Municipal surtax calculated |
| `H28` | Add.Com.Agg. da tratt. | Municipal surtax remaining |
| `H30` | Add.Com.Agg. rata | Municipal surtax installment |
| `H36` | Accon.Add.Com.Agg. Dovuto | Municipal surtax advance payment due |
| `H37` | Accon.Add.Com.Agg. Tratt. | Municipal surtax advance — total installments info |
| `H38` | Acconto Add.Com.Agg. Rata | Municipal surtax advance installment |

#### December-only year-end (conguaglio) rows

| Code | Description | Meaning |
|---|---|---|
| `I59` | Imponibile addizionali | Annual taxable base for regional/municipal surtaxes |
| `I61` | Imponibile IRPEF annuo | Total annual IRPEF taxable income |
| `I64` | Imposta lorda annua | Annual gross IRPEF calculated on full-year income. `ore_gg_num_perc` = top marginal bracket. |
| `I65` | Detrazioni spettanti | Annual tax deductions. `ore_gg_num_perc` = days worked (365 for full year). |
| `I68` | Irpef trattenuta azienda | IRPEF already withheld by the employer during Jan-Nov |
| `I71` | Irpef cod.1001 cong. + | Conguaglio IRPEF — additional tax due at year-end. Positive `ritenute` = employee owes more. |

---

## 5. Interpreting `search_payslip_items()`

Returns grouped results + grand totals.

| Field | Meaning |
|---|---|
| `keyword` | The search term used |
| `results[]` | Array of matches grouped by month/year/codice_voce/descrizione |
| `results[].tot_competenze` | Sum of competenze for that item in that month |
| `results[].tot_ritenute` | Sum of ritenute for that item in that month |
| `results[].occorrenze` | Number of matching rows (usually 1 per month) |
| `grand_total_competenze` | Grand total of all competenze across all results |
| `grand_total_ritenute` | Grand total of all ritenute across all results |

**Important**: Items that only have `dato_figurativo` (like Ticket elettronico) will show `null` for both `tot_competenze` and `tot_ritenute`, and `0` for grand totals. This is correct — they have no cash impact. To analyze meal voucher usage, look at `ore_gg_num_perc` (days) and `dato_figurativo` (total value) in the detailed payslip view instead.

### Useful search keywords

| Keyword | What it finds |
|---|---|
| `Retribuzione` | Base salary entries |
| `Commissioni` | Sales commissions |
| `Straordinario` | Overtime pay |
| `Ferie` | Vacation (taken, accrued, or liquidated) |
| `festiv` | Public holiday pay |
| `13ma` or `mensilita` | 13th/14th month bonus |
| `Malattia` | Sick leave |
| `Ticket` | Meal vouchers |
| `IRPEF` or `Irpef` | Income tax entries |
| `Add.Reg` | Regional surtax |
| `Add.Com` | Municipal surtax |
| `FPLD` or `Contributo` | INPS pension contributions |
| `Fonte` | Supplementary pension fund |
| `TFR` or `tfr` | Severance pay / pension fund transfer |
| `ESPP` | Employee stock purchase plan |
| `Auto` | Car allowance |
| `Ass.Pol` | Insurance policies (life, accident) |
| `Quadrifor` or `Quas` | Manager-specific fund contributions |
| `Esonero` or `Cuneo` | Tax wedge reduction (government subsidy) |
| `R.o.l` or `liquidate` | ROL/leave hours (taken or monetized) |
| `Gym` | Gym/wellness allowance |
| `Bonus` | Discretionary bonus payments |
| `StockO` or `ProfSh` | Stock options / profit sharing (figurative taxable) |
| `Rimb` | Reimbursements (telephone, expenses, IRPEF 730 refund) |
| `Una tantum` | CCNL back-pay from contract renewals |
| `Arretrati` or `A.P.` | Previous-year adjustments (contributions/tax) |
| `Competenze varie` or `Nette varie` | Miscellaneous earnings (taxable or net) |
| `Perm` | Paid leave (permessi) |

---

## 6. Validation Rules & Discrepancy Detection

### 6.1 Monthly checksum

```
totale_competenze - totale_ritenute + arr_attuale - arr_precedente ≈ netto_a_pagare
```
Tolerance: ±0.05€. If `is_valid = 0`, flag immediately.

### 6.2 Base salary consistency

`elementi_retributivi_totale` should be constant across months unless:
- A CCNL renewal changed the minimum pay (usually affects all employees at once)
- A promotion/level change occurred
- A seniority raise (scatto di anzianita) was triggered by tenure milestones

**Action**: If it changes, explain the likely reason based on timing and magnitude.

### 6.3 INPS contribution validation

For each month, verify:
```
Contributo FPLD (voce 150): dato_base × (ore_gg_num_perc / 100) ≈ ritenute
```
The `dato_base` is the rounded INPS base. The unrounded value is in voce `V01`. Tolerance: ±0.02€ for standard months. In months with back-pay adjustments (voce `954`), discrepancies up to ~1€ are normal.

**INPS ceiling split**: When the monthly INPS base exceeds the annual ceiling (massimale), contributions are split into two lines — e.g. voce `51143` (within ceiling) and `51144` (above ceiling). Both use the same rate but different base amounts. Verify each line independently. The sum of their `dato_base` values should approximate the full month's contribution base.

### 6.4 IRPEF validation (monthly)

Monthly taxable income chain (base case):
```
Imponibile INPS (V01 dato_base)
  − Totale ritenute sociali (voce 900 dato_base)
  + Figurative taxable items NOT in V01 (e.g. voce 891 StockO/ProfSh)
  + Non-deductible social contributions included in voce 900 (e.g. voce 044 Quadrifor)
  ≈ Impon. fiscale mese (voce I01 dato_base)
```

**Important nuances**:
- The simple formula `V01 - voce 900 = I01` works for **standard months** with no special items.
- **Stock options / profit sharing** (voce `891`): Taxable for IRPEF but NOT in the INPS base (V01). Their `dato_figurativo` must be added to the IRPEF base. Can create discrepancies of thousands of euros.
- **Quadrifor** (voce `044`): Included in voce 900 but NOT deductible from IRPEF. Add its amount back (~25€ in January).
- **14ma/13ma mensilita** (voce `082`/`081`): When an additional month bonus is paid, the IRPEF base is split: `I01` = regular income, `I02` = bonus. The full check is: `V01 - 900 + adjustments ≈ I01 + I02`. The IRPEF on the bonus appears in `I41` (not I21).
- **Back-pay / arretrati** (voce `266` Una tantum): Previous-year back-pay creates separate tax lanes: `I03` = prior-year taxable, `I48` = prior-year IRPEF (at the prior year's marginal rate). The IRPEF chain becomes: `V01 - 900 + adjustments - I03 ≈ I01`. The I03 amount is taxed independently via I48.
- **Back-pay contribution adjustment** (voce `954`): May cause ~1€ discrepancy in INPS validation (check 6.3) due to retroactive contribution recalculation.
- When you detect a discrepancy, systematically check for: (1) voce 891 figurative, (2) voce I02 bonus split, (3) voce I03 back-pay split, (4) voce 044 non-deductible items, before flagging as an error.

### 6.5 Year-to-date progressive consistency

When comparing month N to month N-1:
```
imp_fiscale(N) ≈ imp_fiscale(N-1) + Impon.fiscale.mese(N)
```
If this doesn't hold, a retroactive adjustment or correction occurred. Flag it.

### 6.6 December conguaglio analysis

December is special. The payroll software recalculates the entire year's taxes:
1. `I61` (Imponibile IRPEF annuo) = total annual IRPEF-taxable income
2. `I64` (Imposta lorda annua) = annual gross IRPEF from tax brackets
3. `I65` (Detrazioni spettanti) = annual deductions (based on 365 days if employed all year)
4. Net annual IRPEF = `I64 dato_base` - `I65 dato_base`
5. Already withheld = `I68 dato_base`
6. Conguaglio = Net annual IRPEF − Already withheld
   - If positive → extra deduction in December (`I71` with ritenute)
   - If negative → refund in December (competenze)

**Common cause of December net pay drop**: Months with high commissions caused the monthly IRPEF estimate to be calculated at a lower marginal bracket. The annual recalculation pushes income into a higher bracket (e.g. from 35% to 43%), resulting in additional tax due.

### 6.7 TFR monitoring

Monthly TFR accrual ≈ `totale_competenze / 13.5` (simplified).

If the employee directs TFR to a pension fund (voce `293` with `ore_gg_num_perc = 100`), the `fdo_tfr_a` field tracks current-year accrual but it gets periodically transferred out. Compare `fdo_tfr_ap` across January payslips year-over-year to see the standing balance.

### 6.8 Addizionali (regional/municipal surtax) pattern

These are calculated on the PREVIOUS year's income and withheld in monthly installments:
- Regional: usually 11 installments (March to January, or similar)
- Municipal: usually 9-11 installments
- Municipal advance: separate installments based on estimated current-year income

The installment number appears in `ore_gg_num_perc` of the rata (installment) voce. Verify that the installment amounts remain constant across months (they should be equal, since the annual total is divided evenly).

### 6.9 Anomaly patterns to watch for

| Anomaly | How to detect | Likely explanation |
|---|---|---|
| Sudden drop in `netto_a_pagare` | Compare month-over-month | High-commission month triggered higher tax bracket; December conguaglio; new deduction started |
| `is_valid = 0` | Direct flag | Parser rounding issue or genuinely incorrect payslip — recommend manual verification |
| `elementi_retributivi_totale` change | Compare month-over-month | Salary increase, promotion, or CCNL renewal |
| Missing month in history | Gap in mese/anno sequence | Could be unpaid leave, delayed payslip, or missing PDF |
| `Commissioni` spike followed by high IRPEF | Cross-reference commission months with I21 ritenute | Progressive taxation — commissions are taxed at the marginal rate in the month received |
| `gg_detr = 365` in December | Check December details | Year-end tax deduction recalculation — normal |
| January extra deductions (`Quadrifor`, `Quas`) | Check January details | Annual fund contributions — normal, only in January |
| Negative `dato_figurativo` on `Smob.TFR Fonte` | Check voce 293 | TFR being transferred to pension fund — not a loss, just a redirect |
| Stock options / profit sharing (voce `891`) | Large `dato_figurativo` with no competenze/ritenute | Taxable phantom income — inflates IRPEF without adding cash. Causes IRPEF chain (6.4) to diverge from the simple formula. NOT a false positive. |
| IRPEF chain mismatch (I01 ≠ V01 - 900) | Check for voce `891`, `044`, or other adjustments | Usually explained by figurative taxable items or non-deductible contributions. See section 6.4 for the full formula. Only flag as error if no explaining items are found. |
| INPS ceiling split (both 51143 + 51144) | Two CIGS/FIS lines in same month | High-income month crossed the INPS annual ceiling — contributions split across two bases. Normal for high earners. |

---

## 7. Communication Guidelines

When presenting payslip data to the user:

1. **Always translate Italian field names** in your explanations: e.g. "netto_a_pagare (net take-home pay)", "totale_competenze (total gross earnings)".

2. **Use the employee's currency**: all amounts are in EUR (€). Format with 2 decimal places and thousands separators.

3. **Contextualize variable pay**: When commissions or bonuses appear, explain that the higher tax rate on those months is due to Italy's progressive IRPEF brackets (23% up to ~28k, 35% up to ~50k, 43% above ~50k on annualized income).

4. **Be specific about figurative items**: Clearly distinguish between cash items (competenze/ritenute) and figurative items (dato_figurativo only). The user should understand that figurative items affect their tax calculation but not their bank balance.

5. **Flag the `banca_ore` field caveat**: The current parser may not accurately capture this field. If the user asks about hours bank, advise checking the original PDF.

6. **Explain seasonal patterns proactively**:
   - January: Annual fund contributions (Quadrifor, QuAS) reduce net pay slightly
   - March onwards: Regional/municipal surtax installments begin
   - June/July: Possible 14ma mensilita (if applicable per CCNL)
   - December: 13ma mensilita + conguaglio = highly variable month

7. **When validating, show your math**: Don't just say "the payslip is correct." Show the calculation chain so the user can follow along.

8. **Privacy**: Never expose raw `codice_fiscale`, `iban`, or `pdf_sha256` unless the user specifically asks. Refer to the employee by name.
