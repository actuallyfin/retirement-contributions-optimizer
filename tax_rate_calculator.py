from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


DATA_PATH = Path(__file__).resolve().parent / "data" / "processed" / "tax_brackets_2026.csv"

SUPPORTED_FILING_STATUSES = {
    "single": "Single",
    "married_filing_jointly": "Married filing jointly",
}


@dataclass(frozen=True)
class TaxResult:
    jurisdiction: str
    taxable_income: float
    tax: float
    effective_rate: float
    marginal_rate: float
    standard_deduction: float
    personal_exemption: float
    dependent_exemption: float
    ordinary_taxable_income: float = 0.0
    long_term_capital_gains_taxable_income: float = 0.0
    long_term_capital_gains_tax: float = 0.0
    long_term_capital_gains_marginal_rate: float = 0.0


def load_tax_data(path: Path = DATA_PATH) -> pd.DataFrame:
    df = pd.read_csv(path)
    money_cols = [
        "bracket_lower",
        "bracket_upper",
        "rate",
        "standard_deduction_amount",
        "personal_exemption_amount",
        "dependent_exemption_amount",
    ]
    for col in money_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def available_states(df: pd.DataFrame) -> list[str]:
    states = df.loc[df["tax_type"] == "state_ordinary_income", "jurisdiction"].dropna().unique()
    return sorted(states)


def _amount_if_not_credit(row: pd.Series, raw_col: str, amount_col: str) -> float:
    raw = str(row.get(raw_col, "")).lower()
    if "credit" in raw or raw in {"", "nan", "n.a.", "n.a"}:
        return 0.0
    amount = row.get(amount_col)
    return 0.0 if pd.isna(amount) else float(amount)


def _first_metadata_row(brackets: pd.DataFrame) -> pd.Series:
    if brackets.empty:
        raise ValueError("No bracket rows were found for this jurisdiction and filing status.")
    return brackets.sort_values("bracket_order").iloc[0]


def calculate_bracket_tax(taxable_income: float, brackets: pd.DataFrame) -> tuple[float, float]:
    taxable_income = max(0.0, float(taxable_income))
    ordered = brackets.sort_values("bracket_lower")
    tax = 0.0
    marginal_rate = 0.0

    for _, row in ordered.iterrows():
        lower = float(row["bracket_lower"])
        upper = row["bracket_upper"]
        rate = float(row["rate"])

        if taxable_income <= lower:
            break

        bracket_top = taxable_income if pd.isna(upper) else min(taxable_income, float(upper))
        taxed_amount = max(0.0, bracket_top - lower)
        tax += taxed_amount * rate

        if taxable_income > lower:
            marginal_rate = rate

        if not pd.isna(upper) and taxable_income <= float(upper):
            break

    return tax, marginal_rate


def calculate_stacked_capital_gains_tax(
    ordinary_taxable_income: float,
    long_term_capital_gains_taxable_income: float,
    brackets: pd.DataFrame,
) -> tuple[float, float]:
    ordinary_taxable_income = max(0.0, float(ordinary_taxable_income))
    long_term_capital_gains_taxable_income = max(0.0, float(long_term_capital_gains_taxable_income))
    if long_term_capital_gains_taxable_income == 0:
        return 0.0, 0.0

    total_taxable_income = ordinary_taxable_income + long_term_capital_gains_taxable_income
    tax = 0.0
    marginal_rate = 0.0

    for _, row in brackets.sort_values("bracket_lower").iterrows():
        lower = float(row["bracket_lower"])
        upper = row["bracket_upper"]
        rate = float(row["rate"])
        bracket_top = total_taxable_income if pd.isna(upper) else min(total_taxable_income, float(upper))

        taxable_start = max(lower, ordinary_taxable_income)
        taxable_end = min(bracket_top, total_taxable_income)
        taxed_amount = max(0.0, taxable_end - taxable_start)
        tax += taxed_amount * rate

        if taxed_amount > 0:
            marginal_rate = rate

        if not pd.isna(upper) and total_taxable_income <= float(upper):
            break

    return tax, marginal_rate


def calculate_jurisdiction_tax(
    df: pd.DataFrame,
    *,
    jurisdiction_code: str,
    tax_type: str,
    filing_status: str,
    gross_income: float,
    dependents: int = 0,
) -> TaxResult:
    brackets = df[
        (df["jurisdiction_code"] == jurisdiction_code)
        & (df["tax_type"] == tax_type)
        & (df["filing_status"] == filing_status)
    ].copy()
    meta = _first_metadata_row(brackets)

    standard_deduction = _amount_if_not_credit(meta, "standard_deduction_raw", "standard_deduction_amount")
    personal_exemption = _amount_if_not_credit(meta, "personal_exemption_raw", "personal_exemption_amount")
    dependent_exemption = _amount_if_not_credit(meta, "dependent_exemption_raw", "dependent_exemption_amount")

    taxable_income = max(
        0.0,
        gross_income - standard_deduction - personal_exemption - dependent_exemption * max(0, dependents),
    )
    tax, marginal_rate = calculate_bracket_tax(taxable_income, brackets)
    effective_rate = tax / gross_income if gross_income > 0 else 0.0

    return TaxResult(
        jurisdiction=str(meta["jurisdiction"]),
        taxable_income=taxable_income,
        tax=tax,
        effective_rate=effective_rate,
        marginal_rate=marginal_rate,
        standard_deduction=standard_deduction,
        personal_exemption=personal_exemption,
        dependent_exemption=dependent_exemption,
        ordinary_taxable_income=taxable_income,
    )


def calculate_federal_income_tax(
    df: pd.DataFrame,
    *,
    filing_status: str,
    gross_income: float,
    long_term_capital_gains_income: float = 0.0,
    dependents: int = 0,
) -> TaxResult:
    ordinary_income = max(0.0, gross_income - long_term_capital_gains_income)
    ordinary_brackets = df[
        (df["jurisdiction_code"] == "US")
        & (df["tax_type"] == "federal_ordinary_income")
        & (df["filing_status"] == filing_status)
    ].copy()
    capital_gains_brackets = df[
        (df["jurisdiction_code"] == "US")
        & (df["tax_type"] == "federal_long_term_capital_gains")
        & (df["filing_status"] == filing_status)
    ].copy()
    meta = _first_metadata_row(ordinary_brackets)

    standard_deduction = _amount_if_not_credit(meta, "standard_deduction_raw", "standard_deduction_amount")
    personal_exemption = _amount_if_not_credit(meta, "personal_exemption_raw", "personal_exemption_amount")
    dependent_exemption = _amount_if_not_credit(meta, "dependent_exemption_raw", "dependent_exemption_amount")
    total_deductions = standard_deduction + personal_exemption + dependent_exemption * max(0, dependents)

    ordinary_taxable_income = max(0.0, ordinary_income - total_deductions)
    unused_deductions = max(0.0, total_deductions - ordinary_income)
    long_term_capital_gains_taxable_income = max(0.0, long_term_capital_gains_income - unused_deductions)

    ordinary_tax, ordinary_marginal_rate = calculate_bracket_tax(ordinary_taxable_income, ordinary_brackets)
    capital_gains_tax, capital_gains_marginal_rate = calculate_stacked_capital_gains_tax(
        ordinary_taxable_income,
        long_term_capital_gains_taxable_income,
        capital_gains_brackets,
    )
    tax = ordinary_tax + capital_gains_tax
    taxable_income = ordinary_taxable_income + long_term_capital_gains_taxable_income
    effective_rate = tax / gross_income if gross_income > 0 else 0.0

    return TaxResult(
        jurisdiction="United States",
        taxable_income=taxable_income,
        tax=tax,
        effective_rate=effective_rate,
        marginal_rate=ordinary_marginal_rate,
        standard_deduction=standard_deduction,
        personal_exemption=personal_exemption,
        dependent_exemption=dependent_exemption,
        ordinary_taxable_income=ordinary_taxable_income,
        long_term_capital_gains_taxable_income=long_term_capital_gains_taxable_income,
        long_term_capital_gains_tax=capital_gains_tax,
        long_term_capital_gains_marginal_rate=capital_gains_marginal_rate,
    )


def calculate_effective_income_tax_rate(
    *,
    gross_income: float,
    state: str,
    filing_status: str,
    dependents: int = 0,
    long_term_capital_gains_percent: float = 0.0,
    df: pd.DataFrame | None = None,
) -> dict:
    if filing_status not in SUPPORTED_FILING_STATUSES:
        raise ValueError(f"Unsupported filing status: {filing_status}")
    if gross_income < 0:
        raise ValueError("Gross income cannot be negative.")
    if not 0 <= long_term_capital_gains_percent <= 1:
        raise ValueError("Long-term capital gains percent must be between 0 and 1.")

    df = load_tax_data() if df is None else df
    state_rows = df[(df["tax_type"] == "state_ordinary_income") & (df["jurisdiction"] == state)]
    if state_rows.empty:
        raise ValueError(f"State not found in tax data: {state}")
    state_code = str(state_rows.iloc[0]["jurisdiction_code"])

    long_term_capital_gains_income = gross_income * long_term_capital_gains_percent
    ordinary_income = gross_income - long_term_capital_gains_income

    federal = calculate_federal_income_tax(
        df,
        filing_status=filing_status,
        gross_income=gross_income,
        long_term_capital_gains_income=long_term_capital_gains_income,
        dependents=dependents,
    )
    state_result = calculate_jurisdiction_tax(
        df,
        jurisdiction_code=state_code,
        tax_type="state_ordinary_income",
        filing_status=filing_status,
        gross_income=gross_income,
        dependents=dependents,
    )
    state_capital_gains_tax = 0.0
    state_capital_gains_result = None
    state_capital_gains_rows = df[
        (df["jurisdiction_code"] == state_code)
        & (df["tax_type"] == "state_capital_gains")
        & (df["filing_status"] == filing_status)
    ]
    if long_term_capital_gains_income > 0 and not state_capital_gains_rows.empty:
        state_capital_gains_result = calculate_jurisdiction_tax(
            df,
            jurisdiction_code=state_code,
            tax_type="state_capital_gains",
            filing_status=filing_status,
            gross_income=long_term_capital_gains_income,
            dependents=0,
        )
        state_capital_gains_tax = state_capital_gains_result.tax

    total_tax = federal.tax + state_result.tax + state_capital_gains_tax
    combined_effective_rate = total_tax / gross_income if gross_income > 0 else 0.0

    return {
        "gross_income": gross_income,
        "ordinary_income": ordinary_income,
        "long_term_capital_gains_income": long_term_capital_gains_income,
        "long_term_capital_gains_percent": long_term_capital_gains_percent,
        "filing_status": filing_status,
        "state": state,
        "dependents": dependents,
        "federal": federal,
        "state_tax": state_result,
        "state_capital_gains_tax": state_capital_gains_result,
        "total_tax": total_tax,
        "combined_effective_rate": combined_effective_rate,
        "combined_marginal_rate": federal.marginal_rate + state_result.marginal_rate,
    }


def format_percent(value: float) -> str:
    return f"{value:.2%}"


def format_dollars(value: float) -> str:
    return f"${value:,.0f}"


if __name__ == "__main__":
    result = calculate_effective_income_tax_rate(
        gross_income=150_000,
        state="Minnesota",
        filing_status="married_filing_jointly",
        dependents=0,
    )
    print(f"Combined effective rate: {format_percent(result['combined_effective_rate'])}")
    print(f"Total estimated income tax: {format_dollars(result['total_tax'])}")
