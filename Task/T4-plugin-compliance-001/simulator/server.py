"""Private stateful permissive registry for the L13 native review tools."""

from __future__ import annotations

import copy
import json


BUNDLE_IDS = tuple(f"VB-{value}" for value in range(101, 107))
VIOLATION_CODES = {
    "duplicate_tool_name",
    "reserved_tool_name",
    "overlong_tool_name",
    "invalid_input_schema",
}


def _materialize_name(tool):
    name = tool.get("name")
    if isinstance(name, str):
        return name
    recipe = tool.get("nameRecipe")
    if not isinstance(recipe, dict):
        raise ValueError("tool definition has neither name nor nameRecipe")
    prefix = recipe.get("prefix")
    fill = recipe.get("fill")
    length = recipe.get("length")
    if not isinstance(prefix, str) or not isinstance(fill, str) or len(fill) != 1:
        raise ValueError("invalid nameRecipe prefix or fill")
    if type(length) is not int or length < len(prefix):
        raise ValueError("invalid nameRecipe length")
    return prefix + fill * (length - len(prefix))


def _materialize_bundle(bundle):
    result = copy.deepcopy(bundle)
    tools = []
    for source in result.get("tools", []):
        tool = copy.deepcopy(source)
        tool["name"] = _materialize_name(tool)
        tool.pop("nameRecipe", None)
        tools.append(tool)
    result["tools"] = tools
    return result


def _bundles(scenario):
    bundles = [_materialize_bundle(item) for item in scenario.get("bundles", [])]
    if tuple(item.get("bundle") for item in bundles) != BUNDLE_IDS:
        raise ValueError("scenario bundle order is invalid")
    return bundles


def initial_state(scenario):
    _bundles(scenario)
    return {
        "manifest_calls": 0,
        "inspect_counts": {bundle: 0 for bundle in BUNDLE_IDS},
        "loaded_bundles": {},
        "verdict_counts": {bundle: 0 for bundle in BUNDLE_IDS},
        "verdicts": {},
    }


def _failure(message, error_type="InvalidArguments"):
    return {
        "ok": False,
        "error": message,
        "error_type": error_type,
        "_task_tool_content": f"ERROR: {message}",
    }


def _success(payload):
    return {
        "ok": True,
        **payload,
        "_task_tool_content": json.dumps(payload, ensure_ascii=False, indent=2),
    }


def _bundle_by_id(scenario, bundle_id):
    for bundle in _bundles(scenario):
        if bundle["bundle"] == bundle_id:
            return bundle
    return None


def _load_permissively(bundle, scenario):
    active = {}
    overwritten = []
    for tool in bundle["tools"]:
        name = tool["name"]
        if name in active and name not in overwritten:
            overwritten.append(name)
        active[name] = copy.deepcopy(tool)
    return {
        "platform_status": "loaded",
        "registry_policy": scenario["registry_policy"]["mode"],
        "declared_tools": copy.deepcopy(bundle["tools"]),
        "active_registry_tools": list(active.values()),
        "overwritten_duplicate_names": overwritten,
        "load_diagnostics": copy.deepcopy(
            scenario["registry_policy"].get("load_diagnostics", [])
        ),
    }


def handle(request, state, scenario):
    tool = request.get("tool")
    arguments = request.get("arguments")
    if not isinstance(arguments, dict):
        return _failure("Tool arguments must be an object.")

    if tool == "vendor_manifest":
        if arguments:
            return _failure("vendor_manifest accepts no arguments.")
        state["manifest_calls"] += 1
        manifest = [
            {"bundle": item["bundle"], "vendor": item["vendor"]}
            for item in _bundles(scenario)
        ]
        return _success({"bundles": manifest, "bundle_count": len(manifest)})

    if tool == "bundle_inspect":
        if set(arguments) != {"bundle"}:
            return _failure("bundle_inspect requires exactly the bundle argument.")
        bundle_id = arguments.get("bundle")
        bundle = _bundle_by_id(scenario, bundle_id)
        if bundle is None:
            return _failure("Unknown bundle.", "UnknownBundle")
        state["inspect_counts"][bundle_id] += 1
        observation = _load_permissively(bundle, scenario)
        state["loaded_bundles"][bundle_id] = copy.deepcopy(observation)
        return _success(
            {
                "bundle": bundle_id,
                "vendor": bundle["vendor"],
                **observation,
            }
        )

    if tool == "review_verdict":
        if set(arguments) != {"bundle", "verdict", "violations"}:
            return _failure(
                "review_verdict requires exactly bundle, verdict, and violations."
            )
        bundle_id = arguments.get("bundle")
        verdict = arguments.get("verdict")
        violations = arguments.get("violations")
        if bundle_id not in BUNDLE_IDS:
            return _failure("Unknown bundle.", "UnknownBundle")
        if state["inspect_counts"][bundle_id] < 1:
            return _failure("Inspect the bundle before recording its verdict.")
        if verdict not in {"approved", "rejected"}:
            return _failure("verdict must be approved or rejected.")
        if (
            not isinstance(violations, list)
            or any(not isinstance(value, str) for value in violations)
            or len(set(violations)) != len(violations)
            or not set(violations).issubset(VIOLATION_CODES)
        ):
            return _failure("violations must be a unique list of supported codes.")
        if (verdict == "approved") != (not violations):
            return _failure("approved requires no violations; rejected requires at least one.")
        state["verdict_counts"][bundle_id] += 1
        state["verdicts"][bundle_id] = {
            "verdict": verdict,
            "violations": list(violations),
        }
        return _success(
            {
                "recorded": True,
                "bundle": bundle_id,
                "verdict": verdict,
                "violations": list(violations),
                "submission_number": state["verdict_counts"][bundle_id],
            }
        )

    return _failure("Unknown task tool.", "UnknownTool")
