from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
STATE_HTML = ROOT / "data" / "raw" / "taxfoundation_state_income_tax_2026.html"
FEDERAL_HTML = ROOT / "data" / "raw" / "taxfoundation_federal_tax_brackets_2026.html"
OUT = ROOT / "data" / "processed" / "tax_brackets_2026.csv"

STATE_SOURCE = "Tax Foundation, 2026 State Income Tax Rates and Brackets"
FEDERAL_SOURCE = "IRS Revenue Procedure 2025-32; Tax Foundation, 2026 Tax Brackets"


STATE_ABBR = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
    "Washington DC": "DC",
}


def clean_state_name(value: object) -> str:
    text = "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)
    text = text.strip()
    if text.startswith("- "):
        text = text[2:]
    return re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()


def money_to_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "n.a.", "n.a", "nan", "-"}:
        return None
    match = re.search(r"-?\$?([0-9][0-9,]*(?:\.\d+)?)", text)
    return float(match.group(1).replace(",", "")) if match else None


def rate_to_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip().lower()
    if not text or text in {"none", "nan"} or "capital gains income only" in text:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", text)
    return float(match.group(1)) / 100 if match else None


def raw_text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def add_upper_bounds(rows: list[dict]) -> list[dict]:
    keyed: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        keyed.setdefault((row["jurisdiction_code"], row["tax_type"], row["filing_status"]), []).append(row)

    out: list[dict] = []
    for _, group in keyed.items():
        group.sort(key=lambda r: (r["bracket_lower"], r["bracket_order"]))
        if group and group[0]["bracket_lower"] > 0 and group[0]["rate"] not in (None, 0):
            first = group[0].copy()
            first.update(
                {
                    "rate": 0.0,
                    "bracket_lower": 0.0,
                    "bracket_upper": group[0]["bracket_lower"],
                    "bracket_order": 0,
                    "bracket_label": "implicit_zero_tax_band",
                }
            )
            group.insert(0, first)
        for i, row in enumerate(group):
            row["bracket_order"] = i + 1
            row["bracket_upper"] = group[i + 1]["bracket_lower"] if i + 1 < len(group) else None
            out.append(row)
    return out


def state_rows() -> list[dict]:
    table = pd.read_html(STATE_HTML)[1]
    rows: list[dict] = []
    current_state = None
    current_meta = {}

    for _, rec in table.iterrows():
        state = clean_state_name(rec["State"])
        if not state:
            continue
        if state in STATE_ABBR:
            current_state = state
            current_meta = {
                "standard_deduction_single_raw": raw_text(rec["Standard Deduction (Single)"]),
                "standard_deduction_mfj_raw": raw_text(rec["Standard Deduction (Couple)"]),
                "personal_exemption_single_raw": raw_text(rec["Personal Exemption (Single)"]),
                "personal_exemption_mfj_raw": raw_text(rec["Personal Exemption (Couple)"]),
                "personal_exemption_dependent_raw": raw_text(rec["Personal Exemption (Dependent)"]),
            }
        elif current_state:
            state = current_state
        else:
            continue

        code = STATE_ABBR[state]
        tax_type = "state_capital_gains" if state == "Washington" else "state_ordinary_income"
        note = "Washington rows from the source apply to capital gains income only." if state == "Washington" else ""

        for filing_status, rate_col, bracket_col in [
            ("single", "Single Filer (Rates)", "Single Filer (Brackets)"),
            ("married_filing_jointly", "Married Filing Jointly (Rates)", "Married Filing Jointly (Brackets)"),
        ]:
            rate = rate_to_float(rec[rate_col])
            lower = money_to_float(rec[bracket_col])
            if raw_text(rec[rate_col]).lower() == "none":
                lower = 0.0
                rate = 0.0
            if rate is None or lower is None:
                continue
            deduction_raw = (
                current_meta.get("standard_deduction_single_raw", "")
                if filing_status == "single"
                else current_meta.get("standard_deduction_mfj_raw", "")
            )
            exemption_raw = (
                current_meta.get("personal_exemption_single_raw", "")
                if filing_status == "single"
                else current_meta.get("personal_exemption_mfj_raw", "")
            )
            rows.append(
                {
                    "tax_year": 2026,
                    "jurisdiction_type": "state",
                    "jurisdiction": state,
                    "jurisdiction_code": code,
                    "tax_type": tax_type,
                    "filing_status": filing_status,
                    "bracket_order": 999,
                    "bracket_lower": lower,
                    "bracket_upper": None,
                    "rate": rate,
                    "bracket_label": "",
                    "standard_deduction_raw": deduction_raw,
                    "standard_deduction_amount": money_to_float(deduction_raw),
                    "personal_exemption_raw": exemption_raw,
                    "personal_exemption_amount": money_to_float(exemption_raw),
                    "dependent_exemption_raw": current_meta.get("personal_exemption_dependent_raw", ""),
                    "dependent_exemption_amount": money_to_float(
                        current_meta.get("personal_exemption_dependent_raw", "")
                    ),
                    "source": STATE_SOURCE,
                    "source_url": "https://taxfoundation.org/data/all/state/state-income-tax-rates-2026/",
                    "notes": note,
                }
            )

    rows = add_upper_bounds(rows)

    # Washington has no broad-based ordinary income tax; its source rows are capital-gains-only.
    for filing_status in ["single", "married_filing_jointly"]:
        rows.append(
            {
                "tax_year": 2026,
                "jurisdiction_type": "state",
                "jurisdiction": "Washington",
                "jurisdiction_code": "WA",
                "tax_type": "state_ordinary_income",
                "filing_status": filing_status,
                "bracket_order": 1,
                "bracket_lower": 0.0,
                "bracket_upper": None,
                "rate": 0.0,
                "bracket_label": "no_broad_based_ordinary_income_tax",
                "standard_deduction_raw": "",
                "standard_deduction_amount": None,
                "personal_exemption_raw": "",
                "personal_exemption_amount": None,
                "dependent_exemption_raw": "",
                "dependent_exemption_amount": None,
                "source": STATE_SOURCE,
                "source_url": "https://taxfoundation.org/data/all/state/state-income-tax-rates-2026/",
                "notes": "Added ordinary-income zero row; Tax Foundation's Washington brackets are capital-gains-only.",
            }
        )

    return rows


def federal_rows() -> list[dict]:
    tables = pd.read_html(FEDERAL_HTML)
    deductions = tables[1]
    deduction_map = {
        "single": money_to_float(deductions.loc[deductions["Filing Status"] == "Single", "Deduction Amount"].iloc[0]),
        "married_filing_jointly": money_to_float(
            deductions.loc[deductions["Filing Status"] == "Married Filing Jointly", "Deduction Amount"].iloc[0]
        ),
        "head_of_household": money_to_float(
            deductions.loc[deductions["Filing Status"] == "Head of Household", "Deduction Amount"].iloc[0]
        ),
        "married_filing_separately": money_to_float(
            deductions.loc[deductions["Filing Status"] == "Single", "Deduction Amount"].iloc[0]
        ),
    }

    rows: list[dict] = []
    ordinary_brackets = {
        "single": [
            (0, 12400, 0.10),
            (12400, 50400, 0.12),
            (50400, 105700, 0.22),
            (105700, 201775, 0.24),
            (201775, 256225, 0.32),
            (256225, 640600, 0.35),
            (640600, None, 0.37),
        ],
        "married_filing_jointly": [
            (0, 24800, 0.10),
            (24800, 100800, 0.12),
            (100800, 211400, 0.22),
            (211400, 403550, 0.24),
            (403550, 512450, 0.32),
            (512450, 768700, 0.35),
            (768700, None, 0.37),
        ],
        "head_of_household": [
            (0, 17700, 0.10),
            (17700, 67450, 0.12),
            (67450, 105700, 0.22),
            (105700, 201750, 0.24),
            (201750, 256200, 0.32),
            (256200, 640600, 0.35),
            (640600, None, 0.37),
        ],
        "married_filing_separately": [
            (0, 12400, 0.10),
            (12400, 50400, 0.12),
            (50400, 105700, 0.22),
            (105700, 201775, 0.24),
            (201775, 256225, 0.32),
            (256225, 384350, 0.35),
            (384350, None, 0.37),
        ],
    }
    for status, brackets in ordinary_brackets.items():
        for i, (lower, upper, rate) in enumerate(brackets):
            rows.append(
                {
                    "tax_year": 2026,
                    "jurisdiction_type": "federal",
                    "jurisdiction": "United States",
                    "jurisdiction_code": "US",
                    "tax_type": "federal_ordinary_income",
                    "filing_status": status,
                    "bracket_order": i + 1,
                    "bracket_lower": float(lower),
                    "bracket_upper": upper,
                    "rate": rate,
                    "bracket_label": "",
                    "standard_deduction_raw": f"${deduction_map[status]:,.0f}",
                    "standard_deduction_amount": deduction_map[status],
                    "personal_exemption_raw": "",
                    "personal_exemption_amount": None,
                    "dependent_exemption_raw": "",
                    "dependent_exemption_amount": None,
                    "source": FEDERAL_SOURCE,
                    "source_url": "https://www.irs.gov/pub/irs-drop/rp-25-32.pdf",
                    "notes": "",
                }
            )

    cap_gains_brackets = {
        "single": [(0, 49450, 0.0), (49450, 545500, 0.15), (545500, None, 0.20)],
        "married_filing_jointly": [(0, 98900, 0.0), (98900, 613700, 0.15), (613700, None, 0.20)],
        "married_filing_separately": [(0, 49450, 0.0), (49450, 306850, 0.15), (306850, None, 0.20)],
        "head_of_household": [(0, 66200, 0.0), (66200, 579600, 0.15), (579600, None, 0.20)],
    }
    for status, brackets in cap_gains_brackets.items():
        for i, (lower, upper, rate) in enumerate(brackets):
            rows.append(
                {
                    "tax_year": 2026,
                    "jurisdiction_type": "federal",
                    "jurisdiction": "United States",
                    "jurisdiction_code": "US",
                    "tax_type": "federal_long_term_capital_gains",
                    "filing_status": status,
                    "bracket_order": i + 1,
                    "bracket_lower": float(lower),
                    "bracket_upper": upper,
                    "rate": rate,
                    "bracket_label": "",
                    "standard_deduction_raw": f"${deduction_map[status]:,.0f}",
                    "standard_deduction_amount": deduction_map[status],
                    "personal_exemption_raw": "",
                    "personal_exemption_amount": None,
                    "dependent_exemption_raw": "",
                    "dependent_exemption_amount": None,
                    "source": FEDERAL_SOURCE,
                    "source_url": "https://www.irs.gov/pub/irs-drop/rp-25-32.pdf",
                    "notes": "",
                }
            )

    return rows


def main() -> None:
    rows = federal_rows() + state_rows()
    df = pd.DataFrame(rows)
    df = df.sort_values(
        ["jurisdiction_type", "jurisdiction", "tax_type", "filing_status", "bracket_order"],
        kind="stable",
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"wrote {OUT} ({len(df)} rows)")
    state_ordinary = df[df["tax_type"] == "state_ordinary_income"]
    print(f"state/DC ordinary jurisdictions: {state_ordinary['jurisdiction_code'].nunique()}")
    print(df.groupby(["jurisdiction_type", "tax_type"])["jurisdiction_code"].nunique().to_string())


if __name__ == "__main__":
    main()
