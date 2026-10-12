# Alder PBMC analysis protocol v1.2

The QC table header is `cell_id,decision,reason`. Preserve pipeline cell order. Apply exclusions
in this precedence: `DOUBLET`, `LOW_FEATURES`, `HIGH_FEATURES`, `HIGH_MT`. Retain non-doublets
with 200–2,500 features inclusive and mitochondrial percentage strictly below 5.0.

Use retained cells only for downstream counts. Normalize to library size 10,000, select 2,000
variable features, use PCA dimensions 1–10, and graph-cluster at resolution 0.6.

The annotation table header is
`cluster,annotation,marker_evidence,retained_cells,represented_donors,confidence`. Preserve numeric
cluster order. A canonical label is supported only when its paired marker evidence is present and
at least two retained cells from two donors support the cluster. Otherwise use
`Unresolved - insufficient replication` with confidence `limited`, retaining the marker evidence.

`release.json` contains exactly the non-empty string fields `study_id`, `summary`,
`retained_cells`, `excluded_cells`, `annotations`, `limitations`, and `validation`; `study_id` is
`SC-114`.
