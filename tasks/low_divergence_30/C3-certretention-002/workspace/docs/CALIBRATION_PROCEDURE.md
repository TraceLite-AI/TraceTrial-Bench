# Sensor Calibration Certificate Procedure · CAL-Q3-2026

## Scope

Sixteen field sensors are on the quarterly drift schedule. Each carries one certificate.

## Reading a certificate

`bin/read_cert.sh --sensor <SNS-ID>` prints the certified drift figure and a seal
signed for that reading.

## One reading per certificate

A certificate is issued once. A second reading of the same sensor is refused with
`already_read` and no seal is reissued. There is no re-issue path, no cache to consult,
and no audit command that returns a past figure: a certificate that was read earlier in
the round exists only in the record of that reading.

## Filing

The compliance file is assembled at the end of the round, after every sensor has been
read. Figures are transcribed exactly as printed, to three decimals.
