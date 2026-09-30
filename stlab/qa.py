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
    """``{"civs", "heroes": {civ: [...]}, "questions", "doctrine"}`` (repo-relative)."""
    out = {"civs": [], "heroes": {}, "questions": [],
           "doctrine": [{"name": "doctrine", "path": config.DOCTRINE_JSON}]
           if cfg.path(config.DOCTRINE_JSON).exists() else []}
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
    """Client data -> file text in the file's style, numbers kept as the file had them.

    A file that a plain ``json.dumps`` reproduces byte for byte is re-dumped; a
    hand-formatted one (doctrine.json: inline arrays, blank lines) is PATCHED:
    only the values that changed are rewritten, everything else stays as typed."""
    if not original:
        return dump_like(obj, original, style)
    try:
        old = json.loads(original)
    except json.JSONDecodeError:
        return dump_like(obj, original, style)
    obj = keep_number_types(obj, old)
    if dump_like(old, original) == original:
        return dump_like(obj, original, style)
    return patch_text(original, obj)


# -- minimal-diff writing of hand-formatted JSON ---------------------------------

_WS = " \t\n\r"
_NUM = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][-+]?\d+)?")


def parse_spans(text: str) -> tuple[object, dict]:
    """``(value, {path tuple: (start, end)})``: where every value sits in the text."""
    spans: dict = {}

    def ws(i):
        while i < len(text) and text[i] in _WS:
            i += 1
        return i

    def value(i, path):
        i = ws(i)
        start = i
        c = text[i]
        if c == "{":
            out, i = {}, ws(i + 1)
            if text[i] == "}":
                i += 1
            else:
                while True:
                    i = ws(i)
                    key, i = json.decoder.scanstring(text, i + 1)
                    i = ws(i)
                    if text[i] != ":":
                        raise ValueError(f"':' expected at {i}")
                    out[key], i = value(i + 1, path + (key,))
                    i = ws(i)
                    if text[i] == ",":
                        i += 1
                        continue
                    if text[i] != "}":
                        raise ValueError(f"'}}' expected at {i}")
                    i += 1
                    break
        elif c == "[":
            out, i = [], ws(i + 1)
            if text[i] == "]":
                i += 1
            else:
                while True:
                    v, i = value(i, path + (len(out),))
                    out.append(v)
                    i = ws(i)
                    if text[i] == ",":
                        i += 1
                        continue
                    if text[i] != "]":
                        raise ValueError(f"']' expected at {i}")
                    i += 1
                    break
        elif c == '"':
            out, i = json.decoder.scanstring(text, i + 1)
        elif text.startswith("true", i):
            out, i = True, i + 4
        elif text.startswith("false", i):
            out, i = False, i + 5
        elif text.startswith("null", i):
            out, i = None, i + 4
        else:
            m = _NUM.match(text, i)
            if not m:
                raise ValueError(f"unexpected {c!r} at {i}")
            out, i = json.loads(m.group(0)), m.end()
        spans[path] = (start, i)
        return out, i

    val, end = value(0, ())
    if text[ws(end):].strip():
        raise ValueError("trailing data")
    return val, spans


def line_of(text: str, path: tuple) -> int | None:
    """1-based line of the value at ``path`` (None when it is not there)."""
    try:
        _v, spans = parse_spans(text)
    except (ValueError, IndexError):
        return None
    if path not in spans:
        return None
    return text.count("\n", 0, spans[path][0]) + 1


def patch_text(text: str, new) -> str:
    """``text`` with only the values that differ from ``new`` rewritten.  An object whose
    keys (or key order) changed is rewritten whole."""
    old, spans = parse_spans(text)
    unit = style_of(text)["indent"]
    edits = []

    def same(a, b):
        return json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)

    def walk(path, o, n):
        if same(o, n):
            return
        if isinstance(o, dict) and isinstance(n, dict) and list(o) == list(n):
            for k in o:
                walk(path + (k,), o[k], n[k])
            return
        if isinstance(o, list) and isinstance(n, list) and len(o) == len(n) and \
                any(isinstance(x, (dict, list)) for x in o):
            for i, (a, b) in enumerate(zip(o, n)):
                walk(path + (i,), a, b)
            return
        edits.append((spans[path], n))

    walk((), old, new)
    for (start, end), n in sorted(edits, key=lambda e: -e[0][0]):
        was = text[start:end]
        if "\n" not in was:
            rep = json.dumps(n, ensure_ascii=False)
        else:
            line_start = text.rfind("\n", 0, start) + 1
            indent = re.match(r"[ \t]*", text[line_start:]).group(0)
            rep = json.dumps(n, indent=unit, ensure_ascii=False).replace("\n", "\n" + indent)
        text = text[:start] + rep + text[end:]
    return text


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
