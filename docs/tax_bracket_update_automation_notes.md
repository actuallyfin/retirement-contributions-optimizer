# Automating Annual Tax Bracket Updates

These notes outline how a future web-based retirement account optimizer could keep federal and state tax bracket data current each year.

## Goal

The app should be able to refresh annual tax data with minimal manual work, while still preserving auditability. Tax brackets are foundational to the model, so the process should favor correctness and traceability over silent automation.

## Recommended Data Pipeline

1. Download official or highly reliable source data.
2. Parse source tables into a normalized intermediate format.
3. Validate coverage and bracket structure.
4. Save versioned CSV/JSON files by tax year.
5. Run model regression tests against known taxpayer examples.
6. Require a human review before publishing updated data to production.

## Candidate Sources

Federal:
- IRS Revenue Procedure for annual inflation adjustments.
- IRS tax bracket pages and Form 1040 instructions.
- Tax Foundation federal tax bracket summaries as a convenient secondary source.

State:
- Tax Foundation annual state income tax brackets table.
- Individual state revenue department forms and instructions.
- NBER TAXSIM or PolicyEngine as a possible cross-check rather than the sole source.

For production, the ideal approach is to treat IRS and state revenue departments as primary sources, with Tax Foundation used as a structured aggregation layer and validation aid.

## File Structure

A useful structure would be:

```text
data/
  raw/
    2026/
      irs_revenue_procedure.pdf
      taxfoundation_state_income_tax.html
  processed/
    tax_brackets_2026.csv
    tax_brackets_2027.csv
  metadata/
    tax_bracket_sources_2026.json
scripts/
  update_tax_brackets.py
  validate_tax_brackets.py
```

The web app should read from `processed` files only. Raw downloads and metadata should be kept for audit/rebuild purposes.

## Normalized Schema

The current CSV schema is a good start:

- `tax_year`
- `jurisdiction_type`
- `jurisdiction`
- `jurisdiction_code`
- `tax_type`
- `filing_status`
- `bracket_order`
- `bracket_lower`
- `bracket_upper`
- `rate`
- `standard_deduction_amount`
- `personal_exemption_amount`
- `dependent_exemption_amount`
- `source`
- `source_url`
- `notes`

Future additions may include:

- `applies_to_income_type`
- `is_flat_tax`
- `has_recapture_rule`
- `local_tax_included`
- `deduction_phaseout_rule_id`
- `credit_rule_id`
- `effective_date`
- `source_publication_date`
- `review_status`

## Validation Checks

Automated validation should catch:

- Missing states or DC.
- Missing federal filing statuses.
- Duplicate bracket lower bounds within a jurisdiction/status/tax type.
- Brackets that are not sorted ascending.
- Negative rates or implausibly high rates.
- Missing top bracket with open upper bound.
- Unexpected changes from prior year, such as a top rate changing by more than a set threshold.
- States moving between no-tax, flat-tax, and graduated-tax categories.
- Washington-style special cases where the tax applies only to capital gains.

The validator should produce a short report that can be reviewed before data is accepted.

## Human Review Still Needed

Some tax rules are difficult to capture from bracket tables alone:

- State-specific standard deduction phaseouts.
- Personal exemption phaseouts.
- Tax benefit recapture rules.
- Local income taxes.
- Retirement income exclusions.
- HSA conformity differences.
- Capital gains treatment differences.
- Credits that materially affect effective tax rates.

For the retirement optimizer, brackets are only one layer. The app will eventually need separate rule tables for deductions, exemptions, credits, retirement income exclusions, and account-specific tax conformity.

## Update Timing

Federal data usually becomes available late in the prior year. State data may lag into the filing year, and some states update brackets after inflation adjustments or legislative sessions.

A practical schedule:

- November or December: update federal brackets.
- January to March: update state brackets from Tax Foundation/state sources.
- April: run a full review before marking the year as production-ready.
- Midyear: check for retroactive state law changes.

## Production Workflow

The safest production flow would be:

1. Run an admin-only update job.
2. Store new data as `draft`.
3. Run validation and model regression tests.
4. Show a diff against the prior year.
5. Require manual approval.
6. Promote the approved file to `active`.
7. Keep the previous year available for reproducibility.

## Model Regression Tests

Each annual update should test:

- Federal tax owed for a few known taxable income levels.
- State tax owed for representative low, middle, and high incomes.
- No-tax states return zero ordinary income tax.
- Flat-tax states apply the expected flat rate.
- Graduated states cross bracket thresholds correctly.
- Retirement withdrawal examples produce stable and explainable changes.

## Longer-Term Option

If the tool becomes serious enough, consider using a rules engine or external tax calculator for full liability estimates. PolicyEngine or TAXSIM could be useful for cross-checking, but the optimizer should still maintain its own lightweight bracket tables for fast explainable projections.

The cleanest architecture is likely:

```text
source fetchers -> parsers -> normalized tax data -> validators -> approved data package -> web app calculator
```

That keeps annual data maintenance separate from the account-ranking logic.
