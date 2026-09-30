"""L8: "+" -- start a new civ from an existing one, and say what cannot work there."""

from __future__ import annotations

import copy
import re

from . import code, config, editor, qa

# Words that mark a text line as still describing the source civ (Sparta).
CIV_WORDS = {
    "spart": ["Sparta", "Spartan", "helot", "Gerousia", "hoplite", "Neodamodes", "Syssiton",
              "Leonidas", "Brasidas", "Agis", "Pausanias"],
}


def _retarget(value, src: str, dst: str):
    """Replace units/<src>/ and structures/<src>/ in every string, recursively."""
    if isinstance(value, str):
        return re.sub(r"\b(units|structures)/" + re.escape(src) + r"/", r"\1/" + dst + "/", value)
    if isinstance(value, list):
        return [_retarget(v, src, dst) for v in value]
    if isinstance(value, dict):
        return {k: _retarget(v, src, dst) for k, v in value.items()}
    return value


def _lines(data: dict) -> list[str]:
    return [str(x.get("text") if isinstance(x, dict) else x) for x in (data.get("text") or [])]


def preview(cfg: config.Config, facts: code.CodeFacts, src: str, dst: str,
            name: str | None = None) -> dict:
    """The files a clone would write and the issues it would have (nothing is written)."""
    issues: list[dict] = []

    def flag(level, what, detail=""):
        issues.append({"level": level, "what": what, "detail": detail})

    codes = {c["code"] for c in qa.civ_codes(cfg)}
    if not config.CIV_CODE_RE.match(dst or ""):
        return {"ok": False, "error": f"{dst!r} is not a civ code", "files": [], "issues": []}
    if dst not in codes:
        return {"ok": False, "error": f"{dst!r} is not a playable civ of the public mod "
                                      f"(codes: {', '.join(sorted(codes))})", "files": [], "issues": []}
    if cfg.path(qa.civ_path(dst)).exists():
        return {"ok": False, "error": f"{qa.civ_path(dst)} already exists", "files": [], "issues": []}
    src_doc = qa.read(cfg, qa.civ_path(src))
    if not src_doc["exists"] or src_doc["data"] is None:
        return {"ok": False, "error": f"no usable civ file for {src!r}", "files": [], "issues": []}

    civ = _retarget(copy.deepcopy(src_doc["data"]), src, dst)
    civ["civ"] = dst
    civ["name"] = name or dst.capitalize()
    files = [{"path": qa.civ_path(dst), "text": qa.dump_like(civ, src_doc["text"])}]
    hero_files = []
    for h in qa.list_files(cfg)["heroes"].get(src, []):
        doc = qa.read(cfg, h["path"])
        if doc["data"] is None:
            flag("cannot work", f"hero file {h['path']} is not valid JSON", doc.get("error") or "")
            continue
        data = _retarget(copy.deepcopy(doc["data"]), src, dst)
        rel = qa.hero_path(dst, h["name"])
        files.append({"path": rel, "text": qa.dump_like(data, doc["text"])})
        hero_files.append({"name": h["name"], "data": data})

    real_heroes = qa.hero_templates_of(cfg, dst)
    hint = f" (see {dst}'s own hero templates below)" if real_heroes else f"; {dst} has no hero templates"
    for h in hero_files:
        for t in h["data"].get("templates") or []:
            if not qa.template_exists(cfg, t, dst):
                flag("cannot work", f"hero {h['name']}: no template {t}", "the hero file describes a unit "
                     f"{dst} cannot train" + hint)
    params = civ.get("params") or {}
    ho = params.get("heroOrder") or {}
    if ho:
        b = ho.get("building")
        if b and not qa.template_exists(cfg, b, dst):
            flag("cannot work", f"hero building {b.replace('{civ}', dst)} does not exist",
                 "hero_next is never asked (no hero building); set params.heroOrder.building")
        for t in ho.get("order") or []:
            if not qa.template_exists(cfg, t, dst):
                flag("cannot work", f"heroOrder.order: no template {t}", "never offered as hero:<x>" + hint)
        u = (ho.get("urgent") or {}).get("hero")
        if u and not qa.template_exists(cfg, u, dst):
            flag("cannot work", f"heroOrder.urgent.hero: no template {u}")
    leader = ((params.get("raid") or {}).get("params") or {}).get("leader")
    if leader and not qa.template_exists(cfg, leader, dst):
        flag("cannot work", f"raid leader {leader.replace('{civ}', dst)} does not exist",
             "the raid group has no leader to train" + hint)
    phalanx = (params.get("params") or {}).get("phalanx")
    if isinstance(phalanx, str) and not qa.template_exists(cfg, phalanx, dst):
        flag("cannot work", f"formation {phalanx} does not exist")
    for tower in ("structures/{civ}/defense_tower", "structures/{civ}/sentry_tower"):
        if not qa.template_exists(cfg, tower, dst):
            flag("check", f"{tower.replace('{civ}', dst)} does not exist",
                 "the tower questions build this template")
    strats = qa.stratagems(cfg)
    for s in qa.civ_stratagems(civ, hero_files):
        d = strats.get(s["kind"])
        if d is None:
            flag("cannot work", f"stratagem {s['kind']} ({s['why']}) is not in doctrine.json")
            continue
        for order in d.get("orders") or []:
            if order not in facts.stratagem_orders.get(s["kind"], []):
                flag("cannot work", f"{s['kind']}/{order}: head.js cannot dispatch it (STRATAGEM_ORDERS)")
            elif order not in facts.order_routes:
                flag("cannot work", f"order {order}: no ORDER_ROUTES entry")
    words = CIV_WORDS.get(src, [src.capitalize()])
    for f in [{"name": dst + ".json", "data": civ}] + [{"name": h["name"] + ".json", "data": h["data"]}
                                                        for h in hero_files]:
        for i, line in enumerate(_lines(f["data"])):
            hits = [w for w in words if w.lower() in line.lower()]
            if hits:
                flag("text", f"{f['name']} line {i + 1} still describes {src}: {', '.join(hits)}",
                     line[:160])
    table_row = (qa.doctrine(cfg).get("civs") or {}).get(dst)
    if table_row:
        flag("note", f"doctrine.json has a row for {dst}", "the new civ file replaces that row")
    return {"ok": True, "src": src, "dst": dst, "files": files, "issues": issues,
            "hero_templates": real_heroes}


def clone(cfg: config.Config, facts: code.CodeFacts, src: str, dst: str, name: str | None = None,
          live_runs: list | None = None) -> dict:
    pv = preview(cfg, facts, src, dst, name)
    if not pv.get("ok"):
        raise editor.Refused("clone", pv.get("error") or "cannot clone")
    n_bad = sum(1 for i in pv["issues"] if i["level"] == "cannot work")
    summary = f"clone {src} into {dst} ({len(pv['files'])} files, {n_bad} thing(s) that cannot work yet)"
    body = "\n".join(f"- [{i['level']}] {i['what']}" for i in pv["issues"][:40])
    res = editor.apply(cfg, facts, [{"path": f["path"], "text": f["text"]} for f in pv["files"]],
                       summary + ("\n\n" + body if body else ""), allow_new=True, allow_flags=True,
                       live_runs=live_runs)
    res["issues"] = pv["issues"]
    return res
