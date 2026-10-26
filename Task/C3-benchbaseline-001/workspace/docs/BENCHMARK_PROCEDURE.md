# Suite Benchmark Baseline Procedure · CAL-Q3-2026

## Scope

Sixteen field suites are on the quarterly baseline schedule. Each carries one baseline.

## Reading a baseline

`bin/read_baseline.sh --suite <BMK-ID>` prints the recorded baseline figure and a seal
signed for that reading.

## One reading per baseline

A baseline is issued once. A second reading of the same suite is refused with
`already_read` and no seal is reissued. There is no re-issue path, no cache to consult,
and no audit command that returns a past figure: a baseline that was read earlier in
the round exists only in the record of that reading.

## Filing

The compliance file is assembled at the end of the round, after every suite has been
read. Figures are transcribed exactly as printed, to three decimals.
