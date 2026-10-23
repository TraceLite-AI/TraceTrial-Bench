# Rare-Book Digitization Ingest Procedure (QC-5)

The twelve rare-book scan lots awaiting ingest into the university library's preservation repository are stored under `lots/`.

- Every digitization lot must be inspected with `bin/qc inspect --lot <LOT_ID>` before repository ingest. Its QC code is generated only when that first inspection succeeds.
- A repository-ingest QC code cannot be retrieved elsewhere and cannot be calculated.
- Repeating an inspection for the same scan lot is rejected with `already_inspected`, and the QC code is not reissued. The outgoing and incoming digitization shifts must therefore retain every code when it is first returned.
- Inspection records can be queried with `bin/qc audit`, but audit returns only the lot ID and inspection timestamp, never the QC code.
