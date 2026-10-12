from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvidenceSelector:
    event_types: tuple[str, ...]
    fields: tuple[str, ...]
    keywords: tuple[str, ...]
    match: str = "any"
    case_sensitive: bool = False
    max_events: int = 20


@dataclass(frozen=True)
class ScoreItem:
    id: str
    type: str
    criterion: str
    evidence: EvidenceSelector | None = None


@dataclass(frozen=True)
class ScoreSheet:
    version: str
    layer: str
    items: tuple[ScoreItem, ...]

    @property
    def rule_items(self) -> tuple[ScoreItem, ...]:
        return tuple(item for item in self.items if item.type == "rule")

    @property
    def rubric_items(self) -> tuple[ScoreItem, ...]:
        return tuple(item for item in self.items if item.type == "rubric")


def load_score_sheet(path: Path, *, expected_layer: str) -> ScoreSheet:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"score sheet is missing or symlinked: {path}")
    try:
        raw = _load_yaml_subset(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"invalid score sheet {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"score sheet must be a mapping: {path}")
    _require_keys(raw, {"version", "layer", "items"}, "score sheet")
    version = raw.get("version")
    layer = raw.get("layer")
    items = raw.get("items")
    if version != "score_v1":
        raise ValueError(f"unsupported score sheet version: {version}")
    if layer != expected_layer:
        raise ValueError(
            f"score sheet layer must be {expected_layer!r}, got {layer!r}"
        )
    if not isinstance(items, list) or not items:
        raise ValueError("score sheet items must be a non-empty list")

    parsed: list[ScoreItem] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        where = f"score sheet item {index + 1}"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be a mapping")
        _require_keys(item, {"id", "type", "criterion", "evidence"}, where)
        item_id = _nonempty_string(item.get("id"), f"{where}.id")
        item_type = _nonempty_string(item.get("type"), f"{where}.type")
        criterion = _nonempty_string(item.get("criterion"), f"{where}.criterion")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item_id):
            raise ValueError(f"{where}.id contains unsafe characters")
        if item_id in seen:
            raise ValueError(f"duplicate score item id: {item_id}")
        seen.add(item_id)
        if item_type not in {"rule", "rubric"}:
            raise ValueError(f"{where}.type must be rule or rubric")
        raw_evidence = item.get("evidence")
        if item_type == "rule":
            if raw_evidence is not None:
                raise ValueError(f"rule item {item_id} must not declare evidence")
            evidence = None
        else:
            evidence = _parse_evidence(raw_evidence, item_id)
        parsed.append(
            ScoreItem(
                id=item_id,
                type=item_type,
                criterion=criterion,
                evidence=evidence,
            )
        )
    return ScoreSheet(version=version, layer=layer, items=tuple(parsed))


def _parse_evidence(value: Any, item_id: str) -> EvidenceSelector:
    if not isinstance(value, dict):
        raise ValueError(f"rubric item {item_id} requires an evidence mapping")
    _require_keys(
        value,
        {
            "event_types",
            "fields",
            "keywords",
            "match",
            "case_sensitive",
            "max_events",
        },
        f"rubric item {item_id}.evidence",
    )
    event_types = _string_list(value.get("event_types"), f"{item_id}.event_types")
    fields = _string_list(value.get("fields"), f"{item_id}.fields")
    keywords = _string_list(value.get("keywords"), f"{item_id}.keywords")
    match = value.get("match", "any")
    if match not in {"any", "all"}:
        raise ValueError(f"{item_id}.evidence.match must be any or all")
    case_sensitive = value.get("case_sensitive", False)
    if not isinstance(case_sensitive, bool):
        raise ValueError(f"{item_id}.evidence.case_sensitive must be boolean")
    max_events = value.get("max_events", 20)
    if (
        not isinstance(max_events, int)
        or isinstance(max_events, bool)
        or not 1 <= max_events <= 100
    ):
        raise ValueError(f"{item_id}.evidence.max_events must be 1..100")
    return EvidenceSelector(
        event_types=event_types,
        fields=fields,
        keywords=keywords,
        match=match,
        case_sensitive=case_sensitive,
        max_events=max_events,
    )


def _require_keys(value: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{where} has unknown fields: {', '.join(unknown)}")


def _nonempty_string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, where: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ValueError(f"{where} must be a non-empty string list")
    return tuple(dict.fromkeys(value))


@dataclass(frozen=True)
class _YamlLine:
    indent: int
    text: str
    number: int


def _load_yaml_subset(text: str) -> Any:
    """Parse the small, explicit YAML subset used by score sheets.

    Supported forms are indentation-based mappings/lists, scalar values, inline
    JSON arrays, and ``>``/``|`` block strings. YAML aliases, tags, implicit
    dates, and other surprising coercions are deliberately unsupported.
    """

    lines: list[_YamlLine] = []
    raw_lines = text.splitlines()
    index = 0
    while index < len(raw_lines):
        raw = raw_lines[index]
        number = index + 1
        index += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent]:
            raise ValueError(f"line {number}: tabs are not allowed")
        body = raw.strip()
        if body.endswith(": >") or body.endswith(": |"):
            key, style = body.rsplit(":", 1)
            block: list[str] = []
            while index < len(raw_lines):
                continuation = raw_lines[index]
                continuation_indent = len(continuation) - len(
                    continuation.lstrip(" ")
                )
                if continuation.strip() and continuation_indent <= indent:
                    break
                index += 1
                if not continuation.strip():
                    block.append("")
                    continue
                block.append(continuation[indent + 2 :].strip())
            value = " ".join(part for part in block if part) if style.strip() == ">" else "\n".join(block)
            body = f"{key}: {json.dumps(value, ensure_ascii=False)}"
        lines.append(_YamlLine(indent=indent, text=body, number=number))
    if not lines:
        return {}
    value, consumed = _parse_yaml_block(lines, 0, lines[0].indent)
    if consumed != len(lines):
        raise ValueError(f"line {lines[consumed].number}: unexpected indentation")
    return value


def _parse_yaml_block(
    lines: list[_YamlLine], index: int, indent: int
) -> tuple[Any, int]:
    if lines[index].indent != indent:
        raise ValueError(f"line {lines[index].number}: invalid indentation")
    if lines[index].text.startswith("- "):
        return _parse_yaml_list(lines, index, indent)
    return _parse_yaml_mapping(lines, index, indent)


def _parse_yaml_mapping(
    lines: list[_YamlLine], index: int, indent: int
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        if line.text.startswith("- ") or ":" not in line.text:
            break
        key, raw_value = line.text.split(":", 1)
        key = key.strip()
        if not key or key in result:
            raise ValueError(f"line {line.number}: invalid or duplicate key")
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            result[key] = _yaml_scalar(raw_value, line.number)
        elif index < len(lines) and lines[index].indent > indent:
            result[key], index = _parse_yaml_block(lines, index, lines[index].indent)
        else:
            result[key] = None
    return result, index


def _parse_yaml_list(
    lines: list[_YamlLine], index: int, indent: int
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        if not line.text.startswith("- "):
            break
        body = line.text[2:].strip()
        index += 1
        if not body:
            if index >= len(lines) or lines[index].indent <= indent:
                raise ValueError(f"line {line.number}: empty list item")
            value, index = _parse_yaml_block(lines, index, lines[index].indent)
            result.append(value)
            continue
        # A quoted scalar or inline JSON collection may legitimately contain a
        # colon (for example, an ``https://`` evidence keyword).  Only plain
        # list items use a colon as the supported ``- key: value`` mapping
        # form; let the scalar parser handle explicitly delimited values.
        if body[0] in {'"', "'", "[", "{"} or ":" not in body:
            result.append(_yaml_scalar(body, line.number))
            continue
        key, raw_value = body.split(":", 1)
        item: dict[str, Any] = {key.strip(): _yaml_scalar(raw_value.strip(), line.number)}
        if index < len(lines) and lines[index].indent > indent:
            continuation, index = _parse_yaml_mapping(lines, index, lines[index].indent)
            duplicate = set(item) & set(continuation)
            if duplicate:
                raise ValueError(f"line {line.number}: duplicate list item key")
            item.update(continuation)
        result.append(item)
    return result, index


def _yaml_scalar(value: str, line_number: int) -> Any:
    if value == "":
        return None
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "~"}:
        return None
    if value[0] in {'"', "'", "[", "{"}:
        candidate = value
        if value[0] == "'" and value[-1:] == "'":
            return value[1:-1].replace("''", "'")
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid scalar: {exc}") from exc
    try:
        return int(value)
    except ValueError:
        return value
