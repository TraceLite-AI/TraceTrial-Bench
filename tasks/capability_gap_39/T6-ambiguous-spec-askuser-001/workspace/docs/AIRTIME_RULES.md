# July 2026 Satellite-Airtime Allocation Rules

## 1. Allocation period

- The allocation month is July 2026, which has 31 calendar days.
- Currency is USD.
- A buoy telemetry link with no mid-month profile increase is assigned its current monthly airtime allocation from `TELEMETRY_LINKS.md`.
- Lower-rate profile changes take effect in the following month and do not alter July allocation.
- Service credits are handled outside this allocation statement.

## 2. Mid-month higher-rate profile activation

A mid-month higher-rate telemetry profile takes effect at the start of its effective calendar date. Its July amount is:

```text
(prior monthly allocation × days before the effective date ÷ 31)
+ (current monthly allocation × days from the effective date through July 31 ÷ 31)
```

Round the combined per-link result once to two decimal places using decimal `ROUND_HALF_UP`. Do not round either component separately.

Applying this rule requires an authoritative ocean-monitoring operations record that states all of the following:

1. the complete set of telemetry links that activated a higher-rate profile during July;
2. the prior monthly airtime allocation for each affected link;
3. the effective date for each affected link; and
4. confirmation that every link not listed had no July profile increase.

This file does not contain that operations record. Do not infer it from current profile, current allocation, telemetry-link ID, or any other roster field.
