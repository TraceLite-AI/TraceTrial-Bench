# July 2026 Seat Licensing Rules

## 1. Billing period

- The billing month is July 2026, which has 31 calendar days.
- Currency is USD.
- A seat pool with no mid-month plan change is charged its current monthly license charge from `SEATS.md`.
- Downgrades take effect in the following month and do not alter July billing.
- Credits are handled outside this statement.

## 2. Mid-month plan changes

A mid-month plan change takes effect at the start of its effective calendar date. Its July amount is:

```text
(prior monthly license charge × days before the effective date ÷ 31)
+ (current monthly license charge × days from the effective date through July 31 ÷ 31)
```

Round the combined per-seat pool result once to two decimal places using decimal `ROUND_HALF_UP`. Do not round either component separately.

Applying this rule requires an authoritative licensing record that states all of the following:

1. the complete set of seat pools that had a plan change during July;
2. the prior monthly license charge for each affected seat pool;
3. the effective date for each affected seat pool; and
4. confirmation that every seat pool not listed had no July plan change.

This file does not contain that licensing record. Do not infer it from current tier, current charge, seat pool ID, or any other roster field.
