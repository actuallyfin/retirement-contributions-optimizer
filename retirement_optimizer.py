from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from tax_rate_calculator import calculate_effective_income_tax_rate, load_tax_data


ROTH_IRA_PHASEOUT_2026 = {
    "single": (153_000, 168_000),
    "married_filing_jointly": (242_000, 252_000),
}

TRADITIONAL_IRA_DEDUCTION_PHASEOUT_2026 = {
    "single_active": (81_000, 91_000),
    "married_filing_jointly_active": (129_000, 149_000),
    "married_filing_jointly_spouse_active": (242_000, 252_000),
}

SOCIAL_SECURITY_WAGE_BASE_2026 = 184_500
EMPLOYEE_SOCIAL_SECURITY_RATE = 0.062
EMPLOYEE_MEDICARE_RATE = 0.0145
ADDITIONAL_MEDICARE_RATE = 0.009
ADDITIONAL_MEDICARE_THRESHOLDS = {
    "single": 200_000,
    "married_filing_jointly": 250_000,
}


@dataclass(frozen=True)
class AccountResult:
    account: str
    contribution_today: float
    future_value_before_tax: float
    future_value_after_tax: float
    tax_due_at_withdrawal: float
    current_tax_savings: float
    eligibility_note: str
    assumptions: str
    contribution_lowest_effective_tax_rate: float = 0.0
    contribution_highest_effective_tax_rate: float = 0.0
    withdrawal_lowest_effective_tax_rate: float = 0.0
    withdrawal_highest_effective_tax_rate: float = 0.0


def phaseout_fraction(income: float, lower: float, upper: float) -> float:
    if income <= lower:
        return 1.0
    if income >= upper:
        return 0.0
    return (upper - income) / (upper - lower)


def roth_ira_eligibility_note(income: float, filing_status: str) -> str:
    limits = ROTH_IRA_PHASEOUT_2026.get(filing_status)
    if limits is None:
        return "Roth IRA income phaseout not modeled for this filing status."
    lower, upper = limits
    if income <= lower:
        return "Direct Roth IRA contribution appears income-eligible."
    if income < upper:
        return "Direct Roth IRA contribution may be partially income-limited."
    return "Direct Roth IRA contribution appears income-ineligible; backdoor Roth not modeled."


def traditional_ira_deduction_fraction(
    income: float,
    filing_status: str,
    covered_by_workplace_plan: bool,
    spouse_covered_by_workplace_plan: bool = False,
) -> float:
    if not covered_by_workplace_plan and not spouse_covered_by_workplace_plan:
        return 1.0
    if filing_status == "single" and covered_by_workplace_plan:
        return phaseout_fraction(income, *TRADITIONAL_IRA_DEDUCTION_PHASEOUT_2026["single_active"])
    if filing_status == "married_filing_jointly" and covered_by_workplace_plan:
        return phaseout_fraction(income, *TRADITIONAL_IRA_DEDUCTION_PHASEOUT_2026["married_filing_jointly_active"])
    if filing_status == "married_filing_jointly" and spouse_covered_by_workplace_plan:
        return phaseout_fraction(
            income,
            *TRADITIONAL_IRA_DEDUCTION_PHASEOUT_2026["married_filing_jointly_spouse_active"],
        )
    return 0.0


def traditional_ira_eligibility_note(deduction_fraction: float) -> str:
    if deduction_fraction >= 0.999:
        return "Traditional IRA contribution appears fully deductible under the modeled inputs."
    if deduction_fraction > 0:
        return "Traditional IRA deduction appears partially income-limited."
    return "Traditional IRA deduction appears income-ineligible; modeled as nondeductible basis plus taxable earnings."


def future_value(amount: float, years: int, annual_return: float, annual_expense: float = 0.0) -> float:
    net_return = annual_return - annual_expense
    return amount * (1 + net_return) ** max(0, years)


def total_income_tax(
    *,
    income: float,
    state: str,
    filing_status: str,
    dependents: int,
    long_term_capital_gains_income: float = 0.0,
    df: pd.DataFrame,
) -> float:
    if income <= 0:
        return 0.0
    ltcg_percent = long_term_capital_gains_income / income if income else 0.0
    result = calculate_effective_income_tax_rate(
        gross_income=income,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        long_term_capital_gains_percent=ltcg_percent,
        df=df,
    )
    return float(result["total_tax"])


def incremental_ordinary_tax(
    *,
    baseline_income: float,
    additional_ordinary_income: float,
    state: str,
    filing_status: str,
    dependents: int,
    df: pd.DataFrame,
) -> float:
    if additional_ordinary_income <= 0:
        return 0.0
    base_tax = total_income_tax(
        income=baseline_income,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    total_tax = total_income_tax(
        income=baseline_income + additional_ordinary_income,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    return max(0.0, total_tax - base_tax)


def ordinary_slice_low_high_rates(
    *,
    baseline_income: float,
    additional_ordinary_income: float,
    state: str,
    filing_status: str,
    dependents: int,
    df: pd.DataFrame,
) -> tuple[float, float]:
    if additional_ordinary_income <= 0:
        return 0.0, 0.0
    low_increment = min(1.0, additional_ordinary_income)
    high_increment = min(1.0, additional_ordinary_income)
    low_tax = incremental_ordinary_tax(
        baseline_income=baseline_income,
        additional_ordinary_income=low_increment,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    high_tax = incremental_ordinary_tax(
        baseline_income=baseline_income + additional_ordinary_income - high_increment,
        additional_ordinary_income=high_increment,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    return max(0.0, low_tax / low_increment), max(0.0, high_tax / high_increment)


def after_tax_ordinary_income(
    *,
    state: str,
    filing_status: str,
    dependents: int,
    df: pd.DataFrame,
    income: float,
) -> float:
    tax = total_income_tax(
        income=income,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    return max(0.0, income - tax)


def solve_baseline_for_ordinary_withdrawal(
    *,
    gross_withdrawal: float,
    required_after_tax_income: float,
    state: str,
    filing_status: str,
    dependents: int,
    df: pd.DataFrame,
) -> tuple[float, float]:
    if gross_withdrawal <= 0:
        return 0.0, 0.0

    def net_income_with_withdrawal(baseline_income: float) -> float:
        withdrawal_tax = incremental_ordinary_tax(
            baseline_income=baseline_income,
            additional_ordinary_income=gross_withdrawal,
            state=state,
            filing_status=filing_status,
            dependents=dependents,
            df=df,
        )
        return (
            after_tax_ordinary_income(
                income=baseline_income,
                state=state,
                filing_status=filing_status,
                dependents=dependents,
                df=df,
            )
            + gross_withdrawal
            - withdrawal_tax
        )

    if net_income_with_withdrawal(0.0) >= required_after_tax_income:
        baseline_income = 0.0
    else:
        low = 0.0
        high = max(required_after_tax_income * 2, gross_withdrawal * 2, 1.0)
        while net_income_with_withdrawal(high) < required_after_tax_income:
            high *= 2
        for _ in range(60):
            mid = (low + high) / 2
            if net_income_with_withdrawal(mid) < required_after_tax_income:
                low = mid
            else:
                high = mid
        baseline_income = high

    withdrawal_tax = incremental_ordinary_tax(
        baseline_income=baseline_income,
        additional_ordinary_income=gross_withdrawal,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    return baseline_income, withdrawal_tax


def incremental_long_term_capital_gains_tax(
    *,
    baseline_ordinary_income: float,
    additional_long_term_capital_gains: float,
    state: str,
    filing_status: str,
    dependents: int,
    df: pd.DataFrame,
) -> float:
    if additional_long_term_capital_gains <= 0:
        return 0.0
    base_tax = total_income_tax(
        income=baseline_ordinary_income,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    total_tax = total_income_tax(
        income=baseline_ordinary_income + additional_long_term_capital_gains,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        long_term_capital_gains_income=additional_long_term_capital_gains,
        df=df,
    )
    return max(0.0, total_tax - base_tax)


def long_term_capital_gains_slice_low_high_rates(
    *,
    baseline_ordinary_income: float,
    additional_long_term_capital_gains: float,
    state: str,
    filing_status: str,
    dependents: int,
    df: pd.DataFrame,
) -> tuple[float, float]:
    if additional_long_term_capital_gains <= 0:
        return 0.0, 0.0
    low_increment = min(1.0, additional_long_term_capital_gains)
    high_increment = min(1.0, additional_long_term_capital_gains)
    low_tax = incremental_long_term_capital_gains_tax(
        baseline_ordinary_income=baseline_ordinary_income,
        additional_long_term_capital_gains=low_increment,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    high_tax = incremental_long_term_capital_gains_tax(
        baseline_ordinary_income=baseline_ordinary_income,
        additional_long_term_capital_gains=additional_long_term_capital_gains,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    ) - incremental_long_term_capital_gains_tax(
        baseline_ordinary_income=baseline_ordinary_income,
        additional_long_term_capital_gains=additional_long_term_capital_gains - high_increment,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    return max(0.0, low_tax / low_increment), max(0.0, high_tax / high_increment)


def solve_taxable_brokerage_withdrawal_for_net(
    *,
    account_value: float,
    basis: float,
    required_after_tax_income: float,
    state: str,
    filing_status: str,
    dependents: int,
    df: pd.DataFrame,
) -> tuple[float, float, float, float]:
    if account_value <= 0:
        return 0.0, 0.0, 0.0, required_after_tax_income
    gain_ratio = max(0.0, account_value - basis) / account_value
    taxable_gain = account_value * gain_ratio

    def net_income_with_withdrawal(baseline_income: float) -> float:
        tax = incremental_long_term_capital_gains_tax(
            baseline_ordinary_income=baseline_income,
            additional_long_term_capital_gains=taxable_gain,
            state=state,
            filing_status=filing_status,
            dependents=dependents,
            df=df,
        )
        return (
            after_tax_ordinary_income(
                income=baseline_income,
                state=state,
                filing_status=filing_status,
                dependents=dependents,
                df=df,
            )
            + account_value
            - tax
        )

    if net_income_with_withdrawal(0.0) >= required_after_tax_income:
        baseline_income = 0.0
    else:
        low = 0.0
        high = max(required_after_tax_income * 2, account_value * 2, 1.0)
        while net_income_with_withdrawal(high) < required_after_tax_income:
            high *= 2
        for _ in range(60):
            mid = (low + high) / 2
            if net_income_with_withdrawal(mid) < required_after_tax_income:
                low = mid
            else:
                high = mid
        baseline_income = high

    tax = incremental_long_term_capital_gains_tax(
        baseline_ordinary_income=baseline_income,
        additional_long_term_capital_gains=taxable_gain,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    return account_value, tax, taxable_gain, baseline_income


def income_tax_savings_from_deduction(
    *,
    current_income: float,
    deduction: float,
    state: str,
    filing_status: str,
    dependents: int,
    df: pd.DataFrame,
) -> float:
    if deduction <= 0:
        return 0.0
    before = total_income_tax(
        income=current_income,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    after = total_income_tax(
        income=max(0.0, current_income - deduction),
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    return max(0.0, before - after)


def hsa_payroll_tax_savings(
    *,
    wage_income_before_contribution: float,
    contribution: float,
    filing_status: str,
) -> float:
    wage_income_after_contribution = max(0.0, wage_income_before_contribution - contribution)

    social_security_before = min(wage_income_before_contribution, SOCIAL_SECURITY_WAGE_BASE_2026)
    social_security_after = min(wage_income_after_contribution, SOCIAL_SECURITY_WAGE_BASE_2026)
    social_security_savings = (social_security_before - social_security_after) * EMPLOYEE_SOCIAL_SECURITY_RATE

    medicare_savings = contribution * EMPLOYEE_MEDICARE_RATE

    additional_medicare_threshold = ADDITIONAL_MEDICARE_THRESHOLDS.get(filing_status)
    additional_medicare_savings = 0.0
    if additional_medicare_threshold is not None:
        additional_before = max(0.0, wage_income_before_contribution - additional_medicare_threshold)
        additional_after = max(0.0, wage_income_after_contribution - additional_medicare_threshold)
        additional_medicare_savings = (additional_before - additional_after) * ADDITIONAL_MEDICARE_RATE

    return max(0.0, social_security_savings + medicare_savings + additional_medicare_savings)


def solve_contribution_for_after_tax_cost(
    *,
    target_after_tax_cost: float,
    deductible_fraction: float,
    current_income: float,
    state: str,
    filing_status: str,
    dependents: int,
    df: pd.DataFrame,
    payroll_tax_savings_func: Callable[[float], float] | None = None,
) -> tuple[float, float]:
    low = target_after_tax_cost
    high = target_after_tax_cost * 3

    def after_tax_cost(contribution: float) -> float:
        deductible_amount = contribution * deductible_fraction
        income_tax_savings = income_tax_savings_from_deduction(
            current_income=current_income,
            deduction=deductible_amount,
            state=state,
            filing_status=filing_status,
            dependents=dependents,
            df=df,
        )
        payroll_tax_savings = payroll_tax_savings_func(contribution) if payroll_tax_savings_func else 0.0
        return contribution - income_tax_savings - payroll_tax_savings

    while after_tax_cost(high) < target_after_tax_cost:
        high *= 2

    for _ in range(60):
        mid = (low + high) / 2
        if after_tax_cost(mid) < target_after_tax_cost:
            low = mid
        else:
            high = mid

    contribution = high
    current_tax_savings = contribution - target_after_tax_cost
    return contribution, max(0.0, current_tax_savings)


def income_tax_cost_on_income_slice(
    *,
    current_income: float,
    income_slice: float,
    state: str,
    filing_status: str,
    dependents: int,
    df: pd.DataFrame,
) -> float:
    if income_slice <= 0:
        return 0.0
    before = total_income_tax(
        income=max(0.0, current_income - income_slice),
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    after = total_income_tax(
        income=current_income,
        state=state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    return max(0.0, after - before)


def optimize_incremental_retirement_dollar(
    *,
    current_income: float,
    retirement_income: float,
    current_state: str,
    retirement_state: str,
    filing_status: str,
    dependents: int,
    current_age: int,
    withdrawal_age: int,
    pretax_budget: float = 1_000,
    annual_return: float = 0.07,
    taxable_annual_tax_drag: float = 0.0,
    retirement_account_expense: float = 0.0,
    employer_plan_extra_expense: float = 0.0,
    hsa_extra_expense: float = 0.0,
    hsa_payroll_contribution: bool = True,
    hsa_qualified_medical_percent: float = 0.0,
    covered_by_workplace_plan: bool = True,
    spouse_covered_by_workplace_plan: bool = False,
    has_401k: bool = True,
    has_hsa: bool = True,
    df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    df = load_tax_data() if df is None else df
    years = max(0, withdrawal_age - current_age)
    ira_deduction_fraction = traditional_ira_deduction_fraction(
        current_income,
        filing_status,
        covered_by_workplace_plan,
        spouse_covered_by_workplace_plan,
    )

    results: list[AccountResult] = []

    def ordinary_withdrawal_tax(amount: float) -> float:
        _, tax = solve_baseline_for_ordinary_withdrawal(
            gross_withdrawal=amount,
            required_after_tax_income=retirement_income,
            state=retirement_state,
            filing_status=filing_status,
            dependents=dependents,
            df=df,
        )
        return tax

    current_income_tax_on_budget = income_tax_cost_on_income_slice(
        current_income=current_income,
        income_slice=pretax_budget,
        state=current_state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    contribution_baseline_income = max(0.0, current_income - pretax_budget)
    contribution_income_low_rate, contribution_income_high_rate = ordinary_slice_low_high_rates(
        baseline_income=contribution_baseline_income,
        additional_ordinary_income=pretax_budget,
        state=current_state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    payroll_tax_on_budget = hsa_payroll_tax_savings(
        wage_income_before_contribution=current_income,
        contribution=pretax_budget,
        filing_status=filing_status,
    )
    after_tax_budget = max(0.0, pretax_budget - current_income_tax_on_budget - payroll_tax_on_budget)
    payroll_tax_rate_on_budget = payroll_tax_on_budget / pretax_budget if pretax_budget else 0.0
    taxable_contribution_low_rate = contribution_income_low_rate + payroll_tax_rate_on_budget
    taxable_contribution_high_rate = contribution_income_high_rate + payroll_tax_rate_on_budget

    roth_ira_contribution = after_tax_budget
    roth_ira_fv = future_value(roth_ira_contribution, years, annual_return, retirement_account_expense)
    results.append(
        AccountResult(
            account="Roth IRA",
            contribution_today=roth_ira_contribution,
            future_value_before_tax=roth_ira_fv,
            future_value_after_tax=roth_ira_fv,
            tax_due_at_withdrawal=0.0,
            current_tax_savings=0.0,
            eligibility_note=roth_ira_eligibility_note(current_income, filing_status),
            assumptions="After-tax contribution; qualified withdrawal tax-free.",
            contribution_lowest_effective_tax_rate=taxable_contribution_low_rate,
            contribution_highest_effective_tax_rate=taxable_contribution_high_rate,
        )
    )

    trad_ira_taxable_slice = pretax_budget * (1 - ira_deduction_fraction)
    trad_ira_tax_cost = income_tax_cost_on_income_slice(
        current_income=current_income,
        income_slice=trad_ira_taxable_slice,
        state=current_state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    trad_ira_contribution = max(0.0, pretax_budget - payroll_tax_on_budget - trad_ira_tax_cost)
    trad_ira_tax_savings = max(0.0, current_income_tax_on_budget - trad_ira_tax_cost)
    trad_ira_fv = future_value(trad_ira_contribution, years, annual_return, retirement_account_expense)
    nondeductible_basis = trad_ira_contribution * (1 - ira_deduction_fraction)
    trad_ira_taxable_withdrawal = max(0.0, trad_ira_fv - nondeductible_basis)
    trad_ira_withdrawal_baseline, trad_ira_withdrawal_tax = (
        solve_baseline_for_ordinary_withdrawal(
            gross_withdrawal=trad_ira_taxable_withdrawal,
            required_after_tax_income=retirement_income,
            state=retirement_state,
            filing_status=filing_status,
            dependents=dependents,
            df=df,
        )
    )
    trad_ira_withdrawal_low_rate, trad_ira_withdrawal_high_rate = ordinary_slice_low_high_rates(
        baseline_income=trad_ira_withdrawal_baseline,
        additional_ordinary_income=trad_ira_taxable_withdrawal,
        state=retirement_state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    if nondeductible_basis > 0:
        trad_ira_withdrawal_low_rate = 0.0
    trad_ira_contribution_low_rate = taxable_contribution_low_rate * (1 - ira_deduction_fraction)
    trad_ira_contribution_high_rate = taxable_contribution_high_rate * (1 - ira_deduction_fraction)
    results.append(
        AccountResult(
            account="Traditional IRA",
            contribution_today=trad_ira_contribution,
            future_value_before_tax=trad_ira_fv,
            future_value_after_tax=trad_ira_fv - trad_ira_withdrawal_tax,
            tax_due_at_withdrawal=trad_ira_withdrawal_tax,
            current_tax_savings=trad_ira_tax_savings,
            eligibility_note=traditional_ira_eligibility_note(ira_deduction_fraction),
            assumptions="Deductible portion gets current tax benefit; nondeductible basis is not taxed again.",
            contribution_lowest_effective_tax_rate=trad_ira_contribution_low_rate,
            contribution_highest_effective_tax_rate=trad_ira_contribution_high_rate,
            withdrawal_lowest_effective_tax_rate=trad_ira_withdrawal_low_rate,
            withdrawal_highest_effective_tax_rate=trad_ira_withdrawal_high_rate,
        )
    )

    roth_401k_contribution = after_tax_budget
    roth_401k_fv = future_value(
        roth_401k_contribution,
        years,
        annual_return,
        retirement_account_expense + employer_plan_extra_expense,
    )
    results.append(
        AccountResult(
            account="Roth 401k",
            contribution_today=roth_401k_contribution,
            future_value_before_tax=roth_401k_fv,
            future_value_after_tax=roth_401k_fv,
            tax_due_at_withdrawal=0.0,
            current_tax_savings=0.0,
            eligibility_note="Requires access to a Roth 401k plan." if has_401k else "Not modeled as available: no 401k access selected.",
            assumptions="After-tax contribution; qualified withdrawal tax-free.",
            contribution_lowest_effective_tax_rate=taxable_contribution_low_rate,
            contribution_highest_effective_tax_rate=taxable_contribution_high_rate,
        )
    )

    trad_401k_contribution = max(0.0, pretax_budget - payroll_tax_on_budget)
    trad_401k_tax_savings = current_income_tax_on_budget
    trad_401k_fv = future_value(
        trad_401k_contribution,
        years,
        annual_return,
        retirement_account_expense + employer_plan_extra_expense,
    )
    trad_401k_withdrawal_baseline, trad_401k_withdrawal_tax = (
        solve_baseline_for_ordinary_withdrawal(
            gross_withdrawal=trad_401k_fv,
            required_after_tax_income=retirement_income,
            state=retirement_state,
            filing_status=filing_status,
            dependents=dependents,
            df=df,
        )
    )
    trad_401k_withdrawal_low_rate, trad_401k_withdrawal_high_rate = ordinary_slice_low_high_rates(
        baseline_income=trad_401k_withdrawal_baseline,
        additional_ordinary_income=trad_401k_fv,
        state=retirement_state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    results.append(
        AccountResult(
            account="Traditional 401k",
            contribution_today=trad_401k_contribution,
            future_value_before_tax=trad_401k_fv,
            future_value_after_tax=trad_401k_fv - trad_401k_withdrawal_tax,
            tax_due_at_withdrawal=trad_401k_withdrawal_tax,
            current_tax_savings=trad_401k_tax_savings,
            eligibility_note="Requires access to a traditional 401k plan." if has_401k else "Not modeled as available: no 401k access selected.",
            assumptions="Pre-tax contribution; withdrawal taxed as ordinary income.",
            contribution_lowest_effective_tax_rate=payroll_tax_rate_on_budget,
            contribution_highest_effective_tax_rate=payroll_tax_rate_on_budget,
            withdrawal_lowest_effective_tax_rate=trad_401k_withdrawal_low_rate,
            withdrawal_highest_effective_tax_rate=trad_401k_withdrawal_high_rate,
        )
    )

    hsa_payroll_savings = payroll_tax_on_budget if hsa_payroll_contribution else 0.0
    hsa_contribution = pretax_budget if hsa_payroll_contribution else max(0.0, pretax_budget - payroll_tax_on_budget)
    hsa_tax_savings = current_income_tax_on_budget + hsa_payroll_savings
    hsa_fv = future_value(hsa_contribution, years, annual_return, retirement_account_expense + hsa_extra_expense)
    nonmedical_hsa_amount = hsa_fv * (1 - hsa_qualified_medical_percent)
    hsa_withdrawal_baseline, hsa_withdrawal_tax = solve_baseline_for_ordinary_withdrawal(
        gross_withdrawal=nonmedical_hsa_amount,
        required_after_tax_income=retirement_income,
        state=retirement_state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    hsa_withdrawal_low_rate, hsa_withdrawal_high_rate = ordinary_slice_low_high_rates(
        baseline_income=hsa_withdrawal_baseline,
        additional_ordinary_income=nonmedical_hsa_amount,
        state=retirement_state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    hsa_contribution_rate = 0.0 if hsa_payroll_contribution else payroll_tax_rate_on_budget
    results.append(
        AccountResult(
            account="HSA",
            contribution_today=hsa_contribution,
            future_value_before_tax=hsa_fv,
            future_value_after_tax=hsa_fv - hsa_withdrawal_tax,
            tax_due_at_withdrawal=hsa_withdrawal_tax,
            current_tax_savings=hsa_tax_savings,
            eligibility_note="Requires HSA eligibility." if has_hsa else "Not modeled as available: HSA eligibility not selected.",
            assumptions="Modeled as a retirement withdrawal: payroll HSA contributions get automatic FICA savings when selected; nonmedical distribution after age 65 is taxed as ordinary income with no penalty.",
            contribution_lowest_effective_tax_rate=hsa_contribution_rate,
            contribution_highest_effective_tax_rate=hsa_contribution_rate,
            withdrawal_lowest_effective_tax_rate=hsa_withdrawal_low_rate,
            withdrawal_highest_effective_tax_rate=hsa_withdrawal_high_rate,
        )
    )

    taxable_contribution = after_tax_budget
    taxable_fv = future_value(
        taxable_contribution,
        years,
        annual_return,
        taxable_annual_tax_drag + retirement_account_expense,
    )
    taxable_gain = max(0.0, taxable_fv - taxable_contribution)
    _, taxable_final_tax, taxable_gain_withdrawn, taxable_baseline_income = solve_taxable_brokerage_withdrawal_for_net(
        account_value=taxable_fv,
        basis=taxable_contribution,
        required_after_tax_income=retirement_income,
        state=retirement_state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    taxable_withdrawal_low_rate, taxable_withdrawal_high_rate = long_term_capital_gains_slice_low_high_rates(
        baseline_ordinary_income=taxable_baseline_income,
        additional_long_term_capital_gains=taxable_gain_withdrawn,
        state=retirement_state,
        filing_status=filing_status,
        dependents=dependents,
        df=df,
    )
    if taxable_contribution > 0:
        taxable_withdrawal_low_rate = 0.0
    results.append(
        AccountResult(
            account="Taxable brokerage",
            contribution_today=taxable_contribution,
            future_value_before_tax=taxable_fv,
            future_value_after_tax=taxable_fv - taxable_final_tax,
            tax_due_at_withdrawal=taxable_final_tax,
            current_tax_savings=0.0,
            eligibility_note="No account-specific eligibility restriction modeled.",
            assumptions="After-tax contribution; final unrealized gain taxed as long-term capital gain.",
            contribution_lowest_effective_tax_rate=taxable_contribution_low_rate,
            contribution_highest_effective_tax_rate=taxable_contribution_high_rate,
            withdrawal_lowest_effective_tax_rate=taxable_withdrawal_low_rate,
            withdrawal_highest_effective_tax_rate=taxable_withdrawal_high_rate,
        )
    )

    data = [r.__dict__ for r in results]
    output = pd.DataFrame(data)
    output["pretax_income_contribution_today"] = pretax_budget
    output["post_tax_contribution_today"] = output["contribution_today"]
    output["contribution_effective_tax_rate"] = (
        output["pretax_income_contribution_today"] - output["post_tax_contribution_today"]
    ) / output["pretax_income_contribution_today"].where(
        output["pretax_income_contribution_today"] != 0
    )
    output["withdrawal_effective_tax_rate"] = output["tax_due_at_withdrawal"] / output[
        "future_value_before_tax"
    ].where(output["future_value_before_tax"] != 0)
    output["future_value_no_fees_or_taxes"] = future_value(pretax_budget, years, annual_return, 0.0)
    output["total_fees_and_taxes_paid"] = (
        output["future_value_no_fees_or_taxes"] - output["future_value_after_tax"]
    ).clip(lower=0.0)
    output["total_fees_and_taxes_paid_pct"] = output["total_fees_and_taxes_paid"] / output[
        "future_value_no_fees_or_taxes"
    ].where(output["future_value_no_fees_or_taxes"] != 0)
    output["net_total_growth_pct"] = (output["future_value_after_tax"] / pretax_budget) - 1
    output["net_annualized_growth_pct"] = (
        (output["future_value_after_tax"] / pretax_budget) ** (1 / years) - 1 if years > 0 else output["net_total_growth_pct"]
    )
    output[["contribution_effective_tax_rate", "withdrawal_effective_tax_rate"]] = output[
        ["contribution_effective_tax_rate", "withdrawal_effective_tax_rate"]
    ].fillna(0.0)
    output[
        [
            "total_fees_and_taxes_paid_pct",
            "net_total_growth_pct",
            "net_annualized_growth_pct",
        ]
    ] = output[
        [
            "total_fees_and_taxes_paid_pct",
            "net_total_growth_pct",
            "net_annualized_growth_pct",
        ]
    ].fillna(0.0)
    if not has_401k:
        output.loc[output["account"].isin(["Roth 401k", "Traditional 401k"]), "future_value_after_tax"] = pd.NA
    if not has_hsa:
        output.loc[output["account"] == "HSA", "future_value_after_tax"] = pd.NA
    output["rank"] = output["future_value_after_tax"].rank(
        method="first",
        ascending=False,
        na_option="bottom",
    ).astype("Int64")
    output["years_to_withdrawal"] = years
    output = output.sort_values(["rank", "account"]).reset_index(drop=True)
    return output


if __name__ == "__main__":
    table = optimize_incremental_retirement_dollar(
        current_income=150_000,
        retirement_income=100_000,
        current_state="Minnesota",
        retirement_state="Minnesota",
        filing_status="married_filing_jointly",
        dependents=0,
        current_age=35,
        withdrawal_age=65,
    )
    print(table[["rank", "account", "future_value_after_tax", "eligibility_note"]].to_string(index=False))
