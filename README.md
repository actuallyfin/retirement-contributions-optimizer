# Retirement Contributions Optimizer

A static web tool for comparing the projected after-tax value of an incremental pretax income amount across retirement savings account types:

- Roth IRA
- Traditional IRA
- Roth 401k
- Traditional 401k
- HSA
- Taxable brokerage

The GitHub Pages app runs entirely in the browser from `index.html`, `styles.css`, `app.js`, and `data/processed/tax_brackets_2026.csv`.

## Model Inputs

- Current ordinary income
- Required annual after-tax retirement income
- Filing status
- Dependents
- Current and retirement state
- Current and planned withdrawal age
- Pretax income amount to compare
- Expected annual return
- Account expense and tax drag assumptions
- 401k, HSA, HSA payroll, and workplace plan eligibility toggles

## Current Modeling Assumptions

- Compares an equal pretax income amount today.
- Models contribution-year income tax and payroll tax treatment.
- Models qualified Roth withdrawals as tax-free.
- Models traditional account withdrawals as ordinary income.
- Models HSA withdrawals as nonmedical, penalty-free retirement distributions after age 65.
- Models taxable brokerage gains as long-term capital gains, taxing only the gain portion.
- Uses current-law 2026 tax brackets from the included CSV.
- Does not yet model employer match, RMDs, Social Security taxation, local taxes, itemized deductions, NIIT, or state-specific retirement-income exclusions.

## Run Locally

For the static site:

```bash
python -m http.server 8010
```

Then open `http://127.0.0.1:8010/`.

The original Streamlit prototype is still included:

```bash
pip install -r requirements.txt
streamlit run retirement_optimizer_app.py
```

## GitHub Pages

Target repository:

```text
actuallyfinance/retirement-contributions-optimizer
```

Expected Pages URL after publishing from the repository root:

```text
https://actuallyfinance.github.io/retirement-contributions-optimizer/
```
