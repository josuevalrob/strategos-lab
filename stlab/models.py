"""L7: how each model answered, per Q&A version, across runs.  Counts only: no win
rates, no scores (Strategos goal: the model is not measured against Petra)."""

from __future__ import annotations

from . import runs


def model_label(header: dict) -> str:
    ad = header.get("adapter") or {}
    label = f"{ad.get('name') or '?'} {ad.get('version') or ad.get('model') or ''}".strip()
    if ad.get("prompt_variant"):
        label += f" [{ad['prompt_variant']}]"
    return label


def group(registry: runs.Registry, kinds: list | None = None) -> dict:
    """``{groups: [{model, qa_version, runs, kinds: {kind: {rows, choices: {opt: n}}}}]}``."""
    groups: dict[tuple, dict] = {}
    all_kinds: dict[str, int] = {}
    for run in registry.all():
        mtime = run.mtime()
        for row in run.rows:
            h = run.header_of(row)
            key = (model_label(h), runs.qa_version(h))
            g = groups.setdefault(key, {"model": key[0], "adapter": (h.get("adapter") or {}).get("name"),
                                        "qa_version": key[1], "runs": [], "kinds": {}})
            if run.id not in g["runs"]:
                g["runs"].append(run.id)
                g["first"] = min(g.get("first", mtime), mtime)
                g["last"] = max(g.get("last", mtime), mtime)
            kind = row.get("kind")
            if kinds and kind not in kinds:
                continue
            all_kinds[kind] = all_kinds.get(kind, 0) + 1
            k = g["kinds"].setdefault(kind, {"rows": 0, "choices": {}})
            k["rows"] += 1
            choice = row.get("choice")
            label = choice if choice is not None else "(no answer)"
            k["choices"][label] = k["choices"].get(label, 0) + 1
    out = sorted(groups.values(), key=lambda g: (g["model"], g.get("first", 0)))
    return {"groups": out, "kinds": sorted(all_kinds, key=lambda k: (-all_kinds[k], k))}
