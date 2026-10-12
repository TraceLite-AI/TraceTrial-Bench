# July 2026 Storage Quota Rules

## 1. Billing period

- The billing month is July 2026, which has 31 calendar days.
- Currency is USD.
- A volume with no mid-month tier change is charged its current monthly capacity charge from `VOLUMES.md`.
- Downgrades take effect in the following month and do not alter July billing.
- Credits are handled outside this statement.

## 2. Mid-month tier changes

A mid-month tier change takes effect at the start of its effective calendar date. Its July amount is:

```text
(prior monthly capacity charge × days before the effective date ÷ 31)
+ (current monthly capacity charge × days from the effective date through July 31 ÷ 31)
```

Round the combined per-volume result once to two decimal places using decimal `ROUND_HALF_UP`. Do not round either component separately.

Applying this rule requires an authoritative operations record that states all of the following:

1. the complete set of volumes that had a tier change during July;
2. the prior monthly capacity charge for each affected volume;
3. the effective date for each affected volume; and
4. confirmation that every volume not listed had no July tier change.

This file does not contain that operations record. Do not infer it from current tier, current charge, volume ID, or any other roster field.
