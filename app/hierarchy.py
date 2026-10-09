"""The network hierarchy, rebuilt from per-meter breadcrumbs, plus a report of what does not add up.

The portal has no hierarchy endpoint. Every meter's detail page carries its own path (Zone > Circle > Division >
Subdivision > Substation > Feeder > DT), so the tree is rebuilt by merging those paths, and the merge is also where
inconsistencies show up. Nothing is dropped silently: a node found under two parents stays where it was first seen
and the conflict is reported. Pure functions, no I/O.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

LEVEL_ORDER = ("zone", "circle", "division", "subdivision", "substation", "feeder", "dt")

Key = tuple[str, str]  # (level, code)


def build_hierarchy(
    paths: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    meter_counts: Mapping[str, int] | None = None,
    transformers: Iterable[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Merge breadcrumb paths into a tree and cross-check them against the lists.

    `paths` maps a DT code, as the meter list states it, to the breadcrumb of one meter under that DT
    (`[{"level": "zone", "name": "Jaipur Zone 1", "code": "Z-01"}, ...]`). `meter_counts` is the meter list's
    count per DT code, `transformers` the Transformers screen's rows (`code`, `feeder_code`, `capacity_kva`).
    Returns `(roots, issues)`; every issue is `{"type", "id", "detail"}`.
    """
    counts = dict(meter_counts or {})
    transformers = list(transformers)  # read twice below; an iterator would be empty the second time
    issues: list[dict[str, str]] = []
    reported: set[tuple[str, str]] = set()

    def report(kind: str, ident: str, detail: str) -> None:
        if (kind, ident) not in reported:  # one line per problem, however many paths show it
            reported.add((kind, ident))
            issues.append({"type": kind, "id": ident, "detail": detail})

    nodes: dict[Key, dict[str, Any]] = {}
    parent_of: dict[Key, Key | None] = {}
    names: dict[Key, set[str]] = defaultdict(set)

    for dt_code, path in sorted(paths.items()):
        previous: Key | None = None
        seen: list[str] = []
        for step in path:
            level, code, name = step.get("level"), step.get("code"), step.get("name")
            if level not in LEVEL_ORDER:
                report("unknown_level", dt_code, f"the path under {dt_code} has a level we do not know: {level!r}")
                continue
            if code is None:
                report("missing_code", f"{dt_code}/{level}", f"{level} {name!r} under {dt_code} has no code")
                code = f"{level}:{name}"  # synthetic but stable, so the tree stays connected
            key = (str(level), str(code))
            if name:
                names[key].add(str(name))
            if key not in nodes:
                nodes[key] = {"node_id": key[1], "name": name, "node_type": key[0], "children": []}
                parent_of[key] = previous
            elif parent_of[key] != previous:
                first, second = _label(parent_of[key]), _label(previous)
                report(
                    "multiple_parents",
                    key[1],
                    f"{key[0]} {key[1]} is under {first} on one path and under {second} on another",
                )
            previous = key
            seen.append(str(level))
        missing = [level for level in LEVEL_ORDER if level not in seen]
        if missing:
            report("incomplete_path", dt_code, f"the breadcrumb under {dt_code} has no {', '.join(missing)}")
        last = path[-1] if path else None
        if last is not None and last.get("level") == "dt" and last.get("code") not in (None, dt_code):
            ends_at = last.get("code")
            report("dt_mismatch", dt_code, f"the meter list says {dt_code} but the breadcrumb ends at {ends_at}")

    for key, spellings in names.items():
        if len(spellings) > 1:
            report("name_mismatch", key[1], f"{key[0]} {key[1]} is spelled {sorted(spellings)}")

    _cross_check(nodes, parent_of, counts, transformers, report)

    capacity = {str(t.get("code")): t.get("capacity_kva") for t in transformers}
    roots: list[dict[str, Any]] = []
    for key in sorted(nodes, key=lambda k: (LEVEL_ORDER.index(k[0]), k[1])):
        parent = parent_of[key]
        (roots if parent is None else nodes[parent]["children"]).append(nodes[key])
    for node in nodes.values():
        node["children"].sort(key=lambda child: str(child["node_id"]))
        if node["node_type"] == "dt" and capacity.get(node["node_id"]) is not None:
            node["capacity_kva"] = capacity[node["node_id"]]
    for root in roots:
        _count_meters(root, counts)
    return roots, issues


def _label(key: Key | None) -> str:
    return "the top level" if key is None else f"{key[0]} {key[1]}"


def _cross_check(
    nodes: Mapping[Key, Mapping[str, Any]],
    parent_of: Mapping[Key, Key | None],
    counts: Mapping[str, int],
    transformers: Sequence[Mapping[str, Any]],
    report: Any,
) -> None:
    """Compare the tree with the Transformers screen and the meter list."""
    listed = {str(t.get("code")): t for t in transformers}
    for code, transformer in sorted(listed.items()):
        node_key = ("dt", code)
        if node_key not in nodes:
            if counts.get(code, 0) == 0:
                report("dt_without_meters", code, f"transformer {code} is listed but no meter is installed under it")
            continue  # with meters but no node, the caller reports the breadcrumb it could not read
        parent = parent_of[node_key]
        feeder = transformer.get("feeder_code")
        if feeder and (parent is None or parent[1] != feeder):
            report(
                "feeder_mismatch",
                code,
                f"the Transformers screen puts {code} on {feeder}, its breadcrumb on {_label(parent)}",
            )
    unknown = sorted(code for code in counts if code not in listed)
    if listed and unknown:
        report(
            "meter_unknown_dt",
            unknown[0],
            f"meters point at transformers the Transformers screen does not list: {unknown}",
        )


def _count_meters(node: dict[str, Any], counts: Mapping[str, int]) -> int:
    total = (
        counts.get(node["node_id"], 0)
        if node["node_type"] == "dt"
        else sum(_count_meters(child, counts) for child in node["children"])
    )
    node["meter_count"] = total
    return total
