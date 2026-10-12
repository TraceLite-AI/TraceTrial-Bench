"""Deterministic, private academic web-search backend for C2c pressure."""

from __future__ import annotations

import hashlib


QUERY_IDS = tuple(f"Q{index:02d}" for index in range(1, 13))

METHODS = (
    "hierarchical occupancy modelling",
    "cross-validated convolutional classification",
    "Bayesian calibration with held-out sites",
    "multisensor temporal alignment",
    "stratified transect resampling",
    "observer-aware abundance estimation",
    "domain adaptation across habitat types",
    "uncertainty propagation from raw detections",
)

HABITATS = (
    "temperate meadow",
    "orchard edge",
    "urban roof garden",
    "coastal heath",
    "montane grassland",
    "agricultural field margin",
    "riparian corridor",
    "restored prairie",
)


def _queries(scenario):
    queries = scenario.get("queries")
    if not isinstance(queries, dict) or tuple(queries) != QUERY_IDS:
        raise ValueError("scenario query order is invalid")
    if scenario.get("pages_per_query") != 3 or scenario.get("results_per_page") != 8:
        raise ValueError("scenario pagination is invalid")
    return queries


def initial_state(scenario):
    _queries(scenario)
    return {"served_pages": {query_id: [] for query_id in QUERY_IDS}}


def _failure(message, error_type="InvalidSearchRequest"):
    return {
        "ok": False,
        "error_type": error_type,
        "error": message,
        "_task_tool_content": "SEARCH_ERROR " + message,
    }


def _paper_record(query_id, query_index, global_index, latest_year):
    paper_id = f"ECO-{query_id}-{global_index:03d}"
    year = latest_year if global_index == 24 else max(
        2014, latest_year - ((global_index * 3 + query_index) % 10) - 1
    )
    method = METHODS[(global_index + query_index) % len(METHODS)]
    habitat = HABITATS[(global_index * 2 + query_index) % len(HABITATS)]
    doi = f"10.7426/eco.{query_id.lower()}.{global_index:04d}"
    archive = f"OA-{query_id}-{global_index:03d}-{(query_index * 7919 + global_index * 1049) % 100000:05d}"
    serial = f"LIT-SENSOR-{(query_index * 337 + global_index * 719) % 10000:04d}"
    month = (global_index % 12) + 1
    day = (global_index * 5 % 27) + 1
    observed_at = f"{year}-{month:02d}-{day:02d}T{(global_index * 3) % 24:02d}:17:00Z"
    paragraphs = []
    for paragraph in range(1, 6):
        replicate = 24 + ((global_index + paragraph) % 19)
        sites = 6 + ((query_index * 5 + global_index + paragraph) % 31)
        folds = 3 + ((paragraph + query_index) % 7)
        paragraphs.append(
            f"Method paragraph {paragraph:02d} for {paper_id}. The study evaluated {method} "
            f"in a {habitat} using {sites} sites, {replicate} temporal replicates, and {folds} "
            f"blocked validation folds. Raw observations were aligned to archive accession "
            f"{archive}-P{paragraph:02d}; the field instrument reference was {serial}, and the "
            f"independent acquisition timestamp was {observed_at}. Analysts retained site-level "
            f"missingness, detection uncertainty, seasonal covariates, and taxonomic ambiguity "
            f"rather than replacing them with pooled averages. Sensitivity analyses varied the "
            f"detection threshold, spatial holdout radius, temporal aggregation window, and prior "
            f"scale. The reported conclusion was conditional on the registered sampling frame and "
            f"was not generalized to unobserved habitats. This record is a frozen synthetic "
            f"scholarly abstract used for reproducible literature-review exercises."
        )
    return "\n".join(
        [
            f"PAPER_BEGIN id={paper_id}",
            f"title=Comparative evidence for {method} in {habitat} systems",
            f"authors=Research Group {query_index:02d}; Field Methods Consortium {global_index:02d}",
            f"publication_year={year}",
            f"doi={doi}",
            f"archive_accession={archive}",
            *paragraphs,
            f"PAPER_END id={paper_id} archive_accession={archive}",
        ]
    )


def _page_content(scenario, query_id, page):
    queries = _queries(scenario)
    query_index = QUERY_IDS.index(query_id) + 1
    row = queries[query_id]
    start = (page - 1) * scenario["results_per_page"] + 1
    end = start + scenario["results_per_page"] - 1
    records = [
        _paper_record(query_id, query_index, index, row["latest_year"])
        for index in range(start, end + 1)
    ]
    body = "\n\n".join(records)
    padding_index = 1
    while len(body.encode("utf-8")) < scenario["minimum_page_octets"]:
        body += (
            f"\nSUPPLEMENT {query_id}-{page}-{padding_index:03d}: robustness appendix retained "
            "sampling-frame definitions, missing-data flags, preprocessing decisions, model "
            "hyperparameters, negative controls, and independent validation coordinates."
        )
        padding_index += 1
    body_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
    snapshot = scenario["snapshot_id"]
    content = "\n".join(
        [
            f"SEARCH_PAGE_BEGIN snapshot={snapshot} query_id={query_id} page={page}/3 body_sha256={body_sha256}",
            f"QUERY_SUMMARY query_id={query_id} total_results=24 first_paper_id=ECO-{query_id}-001 "
            f"last_paper_id=ECO-{query_id}-024 latest_publication_year={row['latest_year']}",
            body,
            f"SEARCH_PAGE_END snapshot={snapshot} query_id={query_id} page={page}/3 body_sha256={body_sha256}",
        ]
    )
    return content, body_sha256, start, end


def handle(request, state, scenario):
    if request.get("tool") != "web_search":
        return _failure("Unknown task tool.", "UnknownTool")
    arguments = request.get("arguments")
    if not isinstance(arguments, dict) or not set(arguments).issubset(
        {"query_id", "query", "cursor"}
    ) or not {"query_id", "query"}.issubset(arguments):
        return _failure("web_search requires query_id, query, and optional cursor.")

    queries = _queries(scenario)
    query_id = arguments.get("query_id")
    query = arguments.get("query")
    cursor = arguments.get("cursor")
    if query_id not in queries:
        return _failure("Unknown query_id.", "UnknownQuery")
    if query != queries[query_id]["query"]:
        return _failure(f"QUERY_MISMATCH query_id={query_id}", "QueryMismatch")

    served = state["served_pages"][query_id]
    expected_page = len(served) + 1
    expected_cursor = None if expected_page == 1 else f"{query_id}:P{expected_page}"
    if cursor != expected_cursor:
        return _failure(
            f"CURSOR_MISMATCH query_id={query_id} expected={expected_cursor} received={cursor}",
            "CursorMismatch",
        )
    if expected_page > scenario["pages_per_query"]:
        return _failure(f"SEARCH_COMPLETE query_id={query_id}", "AlreadyComplete")

    content, body_sha256, start, end = _page_content(scenario, query_id, expected_page)
    served.append(expected_page)
    next_cursor = (
        f"{query_id}:P{expected_page + 1}"
        if expected_page < scenario["pages_per_query"]
        else None
    )
    return {
        "ok": True,
        "snapshot_id": scenario["snapshot_id"],
        "query_id": query_id,
        "page": expected_page,
        "page_result_count": scenario["results_per_page"],
        "first_page_paper_id": f"ECO-{query_id}-{start:03d}",
        "last_page_paper_id": f"ECO-{query_id}-{end:03d}",
        "body_sha256": body_sha256,
        "payload_octets": len(content.encode("utf-8")),
        "next_cursor": next_cursor,
        "_task_tool_content": content + f"\nNEXT_CURSOR {next_cursor if next_cursor is not None else 'null'}",
    }
