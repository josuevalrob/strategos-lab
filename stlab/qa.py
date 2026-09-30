"""The Q&A files: listing, reading, writing in each file's own JSON style."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import config


def sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def list_files(cfg: config.Config) -> dict:
    """``{"civs": [...], "heroes": {civ: [...]}, "questions": [...]}`` (repo-relative)."""
    out = {"civs": [], "heroes": {}, "questions": []}
    civs = cfg.path(config.CIVS_DIR)
    for p in sorted(civs.glob("*.json")) if civs.exists() else []:
        out["civs"].append({"civ": p.stem, "path": f"{config.CIVS_DIR}/{p.name}"})
    heroes = cfg.path(config.HEROES_DIR)
    if heroes.exists():
        for d in sorted(x for x in heroes.iterdir() if x.is_dir()):
            out["heroes"][d.name] = [{"name": p.stem, "path": f"{config.HEROES_DIR}/{d.name}/{p.name}"}
                                     for p in sorted(d.glob("*.json"))]
    qdir = cfg.path(config.QUESTIONS_DIR)
    for p in sorted(qdir.glob("*.json")) if qdir.exists() else []:
        out["questions"].append({"name": p.stem, "path": f"{config.QUESTIONS_DIR}/{p.name}"})
    return out


def civ_path(civ: str) -> str:
    return f"{config.CIVS_DIR}/{civ}.json"


def hero_path(civ: str, name: str) -> str:
    return f"{config.HEROES_DIR}/{civ}/{name}.json"


def question_path(name: str) -> str:
    return f"{config.QUESTIONS_DIR}/{name}.json"


def read(cfg: config.Config, rel: str) -> dict:
    """``{path, exists, text, sha256, data, error}``."""
    p = cfg.path(rel)
    if not p.exists():
        return {"path": rel, "exists": False, "text": None, "sha256": None, "data": None,
                "error": "no such file"}
    text = p.read_text(encoding="utf-8")
    data, error = None, None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        error = f"invalid JSON: {exc}"
    return {"path": rel, "exists": True, "text": text, "sha256": sha256(text), "data": data,
            "error": error}


def load_json(cfg: config.Config, rel: str, default=None):
    p = cfg.path(rel)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def style_of(text: str | None) -> dict:
    """The indentation / escaping / final newline a file was written with."""
    if not text:
        return {"indent": "\t", "ensure_ascii": False, "newline": True}
    m = re.search(r"\n([ \t]+)\S", text)
    return {"indent": m.group(1) if m else "\t", "ensure_ascii": "\\u" in text,
            "newline": text.endswith("\n")}


def keep_number_types(new, old):
    """``new`` with every number that equals the one at the same place in ``old`` written
    as ``old`` wrote it (1.0 stays 1.0 after a trip through the browser, where it is 1)."""
    if isinstance(new, dict) and isinstance(old, dict):
        return {k: keep_number_types(v, old.get(k)) for k, v in new.items()}
    if isinstance(new, list) and isinstance(old, list):
        return [keep_number_types(v, old[i] if i < len(old) else None) for i, v in enumerate(new)]
    if (isinstance(new, (int, float)) and not isinstance(new, bool) and isinstance(old, (int, float))
            and not isinstance(old, bool) and new == old):
        return old
    return new


def dump_data(obj, original: str | None = None, style: dict | None = None) -> str:
    """Client data -> file text in the file's style, numbers kept as the file had them."""
    if original:
        try:
            obj = keep_number_types(obj, json.loads(original))
        except json.JSONDecodeError:
            pass
    return dump_like(obj, original, style)


def dump_like(obj, original: str | None = None, style: dict | None = None) -> str:
    st = style or style_of(original)
    out = json.dumps(obj, indent=st["indent"], ensure_ascii=st["ensure_ascii"])
    return out + ("\n" if st["newline"] else "")


# -- game data the validators and the map need ---------------------------------

def doctrine(cfg: config.Config) -> dict:
    return load_json(cfg, config.DOCTRINE_JSON, {}) or {}


def stratagems(cfg: config.Config) -> dict:
    return doctrine(cfg).get("stratagems") or {}


def template_exists(cfg: config.Config, template: str, civ: str | None = None) -> str | None:
    """Repo-relative path of the template's XML (public or strategos mod), else None."""
    if not isinstance(template, str) or not template:
        return None
    name = template.replace("{civ}", civ or "{civ}")
    if "{" in name or ".." in name:
        return None
    for root in (config.MOD_TEMPLATES, config.PUBLIC_TEMPLATES):
        rel = f"{root}/{name}.xml"
        if cfg.path(rel).exists():
            return rel
    return None


def civ_codes(cfg: config.Config) -> list[dict]:
    """Playable civ codes from the public mod's civ data."""
    out = []
    root = cfg.path(config.PUBLIC_CIVS)
    for p in sorted(root.glob("*.json")) if root.exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        code = d.get("Code") or p.stem
        if d.get("SelectableInGameSetup", True) is False:
            continue
        out.append({"code": code, "culture": d.get("Culture") or "",
                    "has_civ_file": cfg.path(civ_path(code)).exists()})
    return out


def hero_templates_of(cfg: config.Config, civ: str) -> list[str]:
    """Hero unit templates the civ has (public + strategos mod): units/<civ>/hero_*."""
    found = set()
    for root in (config.PUBLIC_TEMPLATES, config.MOD_TEMPLATES):
        d = cfg.path(f"{root}/units/{civ}")
        if d.exists():
            for p in d.glob("hero_*.xml"):
                found.add(f"units/{civ}/{p.stem}")
    return sorted(found)


def hero_option(template: str) -> str:
    """head.js heroOption: "units/spart/hero_agis" -> "hero_agis"."""
    return template[template.rfind("/") + 1:]


def civ_stratagems(civ_data: dict, hero_files: list[dict]) -> list[dict]:
    """The stratagems a civ can play: [{kind, why}], civ doctrine first."""
    params = (civ_data or {}).get("params") or {}
    seen, out = set(), []

    def add(kind, why):
        if isinstance(kind, str) and kind and kind not in seen:
            seen.add(kind)
            out.append({"kind": kind, "why": why})

    add(params.get("doctrine"), "params.doctrine (civ)")
    add(params.get("withoutChoke") or ((params.get("ground") or {}).get("withoutChoke")),
        "params.withoutChoke (no pass on the map)")
    for h in hero_files:
        hp = (h.get("data") or {}).get("params") or {}
        add(hp.get("playbook"), f"hero {h.get('name')}: params.playbook")
        add((hp.get("ground") or {}).get("withoutChoke"), f"hero {h.get('name')}: ground.withoutChoke")
    return out
