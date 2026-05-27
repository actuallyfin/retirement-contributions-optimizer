from __future__ import annotations

import pandas as pd
import streamlit as st

from retirement_optimizer import optimize_incremental_retirement_dollar
from tax_rate_calculator import SUPPORTED_FILING_STATUSES, available_states, format_dollars, format_percent, load_tax_data


st.set_page_config(page_title="Retirement Account Optimizer", page_icon="$", layout="wide")


@st.cache_data
def cached_tax_data() -> pd.DataFrame:
    return load_tax_data()


def money(value: float | int | None) -> str:
    if pd.isna(value):
        return "Not available"
    return format_dollars(float(value))


def pct(value: float | int) -> float:
    return float(value) / 100


df = cached_tax_data()
states = available_states(df)

st.title("Retirement Account Optimizer")

with st.form("optimizer_inputs"):
    left, right = st.columns(2)

    with left:
        current_income = st.number_input("Current ordinary income", min_value=0, value=150_000, step=5_000)
        retirement_income = st.number_input(
            "Required annual after-tax retirement income",
            min_value=0,
            value=100_000,
            step=5_000,
            help="Taxable account withdrawals are modeled as the final slice needed to reach this after-tax spending target.",
        )
        filing_status = st.selectbox(
            "Filing status",
            options=list(SUPPORTED_FILING_STATUSES.keys()),
            format_func=SUPPORTED_FILING_STATUSES.get,
        )
        dependents = st.number_input("Dependents", min_value=0, value=0, step=1)

    with right:
        default_state_index = states.index("Minnesota") if "Minnesota" in states else 0
        current_state = st.selectbox("Current state", options=states, index=default_state_index)
        retirement_state = st.selectbox("Retirement state", options=states, index=default_state_index)
        current_age = st.number_input("Current age", min_value=0, max_value=120, value=35, step=1)
        withdrawal_age = st.number_input("Planned withdrawal age", min_value=0, max_value=120, value=65, step=1)

    with st.expander("Advanced options"):
        pretax_budget = st.number_input("Pretax income amount to compare", min_value=1, value=3_000, step=100)
        annual_return = pct(st.slider("Expected annual investment return", 0.0, 15.0, 7.0, 0.25))
        retirement_account_expense = pct(st.slider("Base annual account expense drag", 0.0, 3.0, 0.10, 0.05))
        employer_plan_extra_expense = pct(st.slider("Extra 401k annual expense drag", 0.0, 3.0, 0.20, 0.05))
        taxable_annual_tax_drag = pct(st.slider("Taxable brokerage annual tax drag", 0.0, 3.0, 0.0, 0.05))
        hsa_extra_expense = pct(st.slider("Extra HSA annual expense drag", 0.0, 3.0, 0.20, 0.05))

        st.divider()
        has_401k = st.checkbox("User has access to a 401k plan", value=True)
        has_hsa = st.checkbox("User is HSA eligible", value=True)
        hsa_payroll_contribution = st.checkbox(
            "HSA contribution will be made through paycheck",
            value=True,
            help="Payroll HSA contributions can avoid employee Social Security and Medicare tax. The app estimates this automatically from income and 2026 payroll tax thresholds.",
        )
        covered_by_workplace_plan = st.checkbox("IRA contributor is covered by a workplace retirement plan", value=True)
        spouse_covered_by_workplace_plan = st.checkbox("Spouse is covered by a workplace retirement plan", value=True)

    submitted = st.form_submit_button("Rank accounts")

if submitted or True:
    results = optimize_incremental_retirement_dollar(
        current_income=float(current_income),
        retirement_income=float(retirement_income),
        current_state=current_state,
        retirement_state=retirement_state,
        filing_status=filing_status,
        dependents=int(dependents),
        current_age=int(current_age),
        withdrawal_age=int(withdrawal_age),
        pretax_budget=float(pretax_budget),
        annual_return=annual_return,
        taxable_annual_tax_drag=taxable_annual_tax_drag,
        retirement_account_expense=retirement_account_expense,
        employer_plan_extra_expense=employer_plan_extra_expense,
        hsa_extra_expense=hsa_extra_expense,
        hsa_payroll_contribution=hsa_payroll_contribution,
        hsa_qualified_medical_percent=0.0,
        covered_by_workplace_plan=covered_by_workplace_plan,
        spouse_covered_by_workplace_plan=spouse_covered_by_workplace_plan,
        has_401k=has_401k,
        has_hsa=has_hsa,
        df=df,
    )

    display = results.copy()
    footnotes = []
    traditional_ira_ineligible = (
        display.loc[display["account"] == "Traditional IRA", "eligibility_note"]
        .astype(str)
        .str.contains("income-ineligible", case=False, na=False)
        .any()
    )
    if traditional_ira_ineligible:
        display.loc[display["account"] == "Traditional IRA", "account"] = "Traditional IRA¹"
        footnotes.append(
            "¹ Traditional IRA deduction appears income-ineligible under these inputs, so the model treats the "
            "contribution as nondeductible basis with taxable growth."
        )

    rows = []
    metrics = [
        ("Rank", lambda row: str(row["rank"])),
        ("Available after withdrawal", lambda row: money(row["future_value_after_tax"])),
        ("Pretax contribution today", lambda row: money(row["pretax_income_contribution_today"])),
        ("Post-tax contribution today", lambda row: money(row["post_tax_contribution_today"])),
        ("Contribution effective tax rate", lambda row: format_percent(row["contribution_effective_tax_rate"])),
        ("Contribution lowest effective tax rate", lambda row: format_percent(row["contribution_lowest_effective_tax_rate"])),
        ("Contribution highest effective tax rate", lambda row: format_percent(row["contribution_highest_effective_tax_rate"])),
        ("Current tax savings", lambda row: money(row["current_tax_savings"])),
        ("Future value before tax", lambda row: money(row["future_value_before_tax"])),
        ("Tax due at withdrawal", lambda row: money(row["tax_due_at_withdrawal"])),
        ("Future value after tax", lambda row: money(row["future_value_after_tax"])),
        ("Withdrawal effective tax rate", lambda row: format_percent(row["withdrawal_effective_tax_rate"])),
        ("Withdrawal lowest effective tax rate", lambda row: format_percent(row["withdrawal_lowest_effective_tax_rate"])),
        ("Withdrawal highest effective tax rate", lambda row: format_percent(row["withdrawal_highest_effective_tax_rate"])),
        ("Total fees and tax impact", lambda row: money(row["total_fees_and_taxes_paid"])),
        ("Total fees and tax impact %", lambda row: format_percent(row["total_fees_and_taxes_paid_pct"])),
        ("Net total growth %", lambda row: format_percent(row["net_total_growth_pct"])),
        ("Net annualized growth %", lambda row: format_percent(row["net_annualized_growth_pct"])),
        ("Eligibility note", lambda row: str(row["eligibility_note"])),
        ("Modeled assumptions", lambda row: str(row["assumptions"])),
    ]
    for metric_name, formatter in metrics:
        row = {"Metric": metric_name}
        for _, account_row in display.iterrows():
            row[str(account_row["account"])] = formatter(account_row)
        rows.append(row)

    comparison_table = pd.DataFrame(rows)
    st.markdown(
        comparison_table.to_html(index=False, escape=False),
        unsafe_allow_html=True,
    )

    for note in footnotes:
        st.caption(note)

    best = results.loc[results["future_value_after_tax"].notna()].sort_values("future_value_after_tax", ascending=False).iloc[0]
    st.metric("Top modeled account", best["account"], money(best["future_value_after_tax"]))

    st.caption(
        "Prototype assumptions: compares an equal pretax income amount today; assumes qualified Roth withdrawals, no early "
        "withdrawal penalties, no RMD interaction, no employer match, no contribution limits except income-driven "
        "IRA eligibility notes, and current-law 2026 tax brackets. Taxable withdrawals are grossed up so the account "
        "value supports the required after-tax retirement income target. Taxable brokerage gains are modeled as "
        "long-term capital gains. HSA withdrawals are modeled as nonmedical retirement distributions after age 65, "
        "so they are taxable as ordinary income but penalty-free."
    )
