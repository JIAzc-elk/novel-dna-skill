#!/usr/bin/env python3
"""Mechanical helpers for novel writing packs; no model calls or AI scoring."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import statistics
import sys
import tempfile
import unicodedata

DIMENSIONS = (
    "语言与叙述", "场景与节奏", "人物与对白", "博弈与关系", "战斗与行动",
    "世界与题材", "修辞与用典", "信息与连载", "情绪与主题",
)
IGNORE = {"readme.md", "agents.md", "skill.md", "pack.md", "chapter_index.md",
          "source_front_matter.md", "split_report.md"}


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_rows(path):
    if not path.exists():
        return []
    result = []
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.strip():
            try:
                item = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path.name}:{n}: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"{path.name}:{n}: expected JSON object")
            result.append(item)
    return result


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                     dir=path.parent, delete=False, suffix=".tmp") as f:
        f.write(text)
        temporary = Path(f.name)
    os.replace(temporary, path)


def write_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_rows(path, rows):
    atomic_text(path, "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode(data):
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("Cannot decode input as UTF-8 or GB18030; convert it explicitly first")


def within(pack, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"Invalid relative path: {relative!r}")
    p = PurePosixPath(relative)
    if p.is_absolute() or PureWindowsPath(relative).drive or ".." in p.parts:
        raise ValueError(f"Path escapes pack: {relative}")
    result = (pack / relative).resolve()
    if not result.is_relative_to(pack.resolve()):
        raise ValueError(f"Path escapes pack (possibly symlink): {relative}")
    return result


def collect(inputs, recursive, output):
    found = []
    for value in inputs:
        p = Path(value).resolve()
        if not p.exists():
            raise ValueError(f"Input does not exist: {p}")
        candidates = sorted(p.rglob("*") if recursive else p.iterdir()) if p.is_dir() else [p]
        for q in candidates:
            if not q.is_file() or q.suffix.lower() not in {".md", ".txt"}:
                continue
            if p.is_dir() and q.name.casefold() in IGNORE:
                continue
            resolved = q.resolve()
            if resolved == output or output in resolved.parents:
                continue
            if q.is_symlink():
                raise ValueError(f"Select the real source explicitly instead of a symlink: {q}")
            if resolved not in found:
                found.append(resolved)
    if not found:
        raise ValueError("No selected .md/.txt source files; pass explicit files or --recursive")
    prepared = []
    for p in found:
        data = p.read_bytes()
        text, encoding = decode(data)
        if not text.strip():
            raise ValueError(f"Empty input: {p}")
        prepared.append((p, data, text, encoding))
    return prepared


def import_prepared(pack, prepared, existing, notes):
    rows = list(existing)
    hashes = {x["sha256"]: x["id"] for x in rows}
    number = max((int(x["id"][1:]) for x in rows), default=0)
    added = 0
    for p, data, text, encoding in prepared:
        sha = digest(data)
        if sha in hashes:
            notes.append({"duplicate_input": p.name, "same_as": hashes[sha]})
            continue
        number += 1
        source_id = f"C{number:04d}"
        relative = f"corpus/{source_id}{p.suffix.lower()}"
        target = within(pack, relative)
        if target.exists():
            raise ValueError(f"Refusing to overwrite unlisted source: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rows.append({"id": source_id, "file": relative, "input_name": p.name,
                     "sha256": sha, "encoding": encoding,
                     "line_count": len(text.splitlines()),
                     "chars_no_space": len(re.sub(r"\s", "", text))})
        hashes[sha] = source_id
        added += 1
    return rows, added


def distribution(values):
    if not values:
        return {"count": 0, "mean": 0, "sd": 0, "median": 0, "p10": 0, "p90": 0}
    values = sorted(values)
    sd = round(statistics.pstdev(values), 2) if len(values) > 1 else 0.0
    return {"count": len(values), "mean": round(statistics.mean(values), 2),
            "sd": sd, "median": statistics.median(values),
            "p10": values[int((len(values)-1)*.1)],
            "p90": values[int((len(values)-1)*.9)]}


MARKERS = (
    "不是", "而是", "并非", "其实", "事实上", "然而", "不过", "只是",
    "顿时", "忽然", "陡然", "猛地", "果然", "居然", "竟然", "莫非",
    "难道", "若是", "倘若", "一旦", "纵然", "即便", "何况", "反倒",
)


def text_stats(text):
    # Quoted text includes thought, quotations and dialogue; it is not a dialogue classifier.
    body = re.sub(r"(?m)^#{1,6}\s+.*$", "", text)
    compact = re.sub(r"\s", "", body)
    raw_paras = [x.strip() for x in body.splitlines() if x.strip()]
    sentences = [len(re.sub(r"[\s“”‘’「」『』]", "", s))
                 for s in re.split(r"[。！？!?]+", body)]
    sentences = [x for x in sentences if x]
    paragraphs = [len(re.sub(r"\s", "", x)) for x in raw_paras]
    single_sent_paras = sum(
        1 for p in raw_paras
        if len([s for s in re.split(r"[。！？!?]+", p) if s.strip()]) <= 1
    )
    quoted = 0
    depth = 0
    for char in compact:
        if char in "“「『":
            depth += 1
        elif char in "”」』":
            depth = max(0, depth-1)
        elif depth:
            quoted += 1
    total_sents = max(1, len(sentences))
    short_sents = sum(1 for x in sentences if x <= 10)
    long_sents = sum(1 for x in sentences if x >= 40)
    scale = 10000.0 / max(1, len(compact))
    marker_rates = {
        m: round(body.count(m) * scale, 2)
        for m in MARKERS if body.count(m) > 0
    }
    sent_dist = distribution(sentences)
    sent_dist["short_le_10_share"] = round(short_sents / total_sents, 4)
    sent_dist["long_ge_40_share"] = round(long_sents / total_sents, 4)
    para_dist = distribution(paragraphs)
    para_dist["single_sentence_share"] = round(single_sent_paras / max(1, len(raw_paras)), 4)
    return {"chars_no_space": len(compact), "sentence_chars": sent_dist,
            "paragraph_chars": para_dist,
            "quoted_char_share": round(quoted/max(1, len(compact)), 4),
            "punctuation": {c: compact.count(c) for c in "，。！？；：—…"},
            "markers_per_10k_chars": marker_rates}


def stats_for_pack(pack):
    sources = read_rows(pack / "corpus.jsonl")
    texts = []
    files = []
    for source in sources:
        data = within(pack, source["file"]).read_bytes()
        text = data.decode(source["encoding"])
        texts.append(text)
        files.append({"source_id": source["id"], **text_stats(text)})
    return {"source_units": len(sources), "meaning": "本批文本的机械统计，不代表语义分析或AI来源判断",
            "aggregate": text_stats("\n\n".join(texts)), "files": files}


def init_pack(args):
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError(f"Output already exists; init never overwrites: {output}")
    prepared = collect(args.input, args.recursive, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Keep a failed staging directory as evidence; never recursively remove user paths.
    staging = Path(tempfile.mkdtemp(prefix=".novel-pack-", dir=output.parent)).resolve()
    notes = []
    sources, count = import_prepared(staging, prepared, [], notes)
    manifest = {"type": "novel-writing-pack", "format_version": 1, "name": args.name,
                "aliases": args.alias or [], "kind": args.kind, "status": "draft",
                "revision": 1, "source_mode": "bundled",
                "scope": "仅本批用户提供的正文；尚未完成语义蒸馏", "modules": [],
                "intake_notes": notes}
    write_json(staging / "manifest.json", manifest)
    write_rows(staging / "corpus.jsonl", sources)
    for name in ("analysis.jsonl", "rules.jsonl", "scenes.jsonl"):
        write_rows(staging / name, [])
    write_json(staging / "coverage.json", {"dimensions": [
        {"dimension": d, "status": "unobserved", "rule_ids": [],
         "note": "尚未完成阅读分析", "suggested_inputs": []} for d in DIMENSIONS]})
    atomic_text(staging / "PACK.md", f"# {args.name}\n\n已收集{count}份文本，尚未完成语义蒸馏。状态以 manifest.json 为准。\n")
    atomic_text(staging / "缺口与补充建议.md", "# 缺口与补充建议\n\n完成本批阅读后，依据实际覆盖填写。\n")
    write_json(staging / "stats.json", stats_for_pack(staging))
    if output.exists():
        raise ValueError(f"Output appeared during collection; staging retained at {staging}")
    staging.rename(output)
    return {"status": "collected", "pack": str(output), "source_units": count,
            "semantic_analysis": "pending", "intake_notes": notes}


def add_sources(args):
    pack = Path(args.pack).resolve()
    manifest = read_json(pack / "manifest.json")
    if manifest.get("type") != "novel-writing-pack" or manifest.get("source_mode") != "bundled":
        raise ValueError("add requires a bundled novel writing pack")
    integrity = validate_pack(pack, allow_draft=True)
    if integrity["errors"]:
        raise ValueError("Repair pack integrity before adding: " + "; ".join(integrity["errors"]))
    prepared = collect(args.input, args.recursive, pack)
    notes = list(manifest.get("intake_notes", []))
    sources, count = import_prepared(pack, prepared, read_rows(pack / "corpus.jsonl"), notes)
    if count:
        manifest["status"] = "draft"
        manifest["revision"] += 1
        manifest["scope"] = "已追加输入；需完成新增阅读并复核指纹和缺口"
        write_rows(pack / "corpus.jsonl", sources)
        write_json(pack / "stats.json", stats_for_pack(pack))
    manifest["intake_notes"] = notes
    write_json(pack / "manifest.json", manifest)
    return {"status": manifest["status"], "added": count, "source_units": len(sources),
            "semantic_analysis": "pending" if count else "unchanged"}


def validate_pack(pack, allow_draft=False):
    errors = []
    def error(message):
        errors.append(message)
    def load_json(name, default):
        try:
            return read_json(within(pack, name))
        except (OSError, ValueError, TypeError) as exc:
            error(f"{name}: {exc}")
            return default
    def load_rows(name, required):
        try:
            p = within(pack, name)
            if required and not p.is_file():
                error(f"Missing {name}")
            return read_rows(p)
        except (OSError, ValueError, TypeError) as exc:
            error(f"{name}: {exc}")
            return []
    m = load_json("manifest.json", {})
    if not isinstance(m, dict):
        return {"status": "FAIL", "errors": ["manifest.json must be an object"]}
    if m.get("type") != "novel-writing-pack" or m.get("format_version") != 1:
        error("Unsupported pack type or format_version")
    for key in ("name", "scope"):
        if not isinstance(m.get(key), str) or not m[key].strip():
            error(f"manifest.{key} must be nonempty text")
    if m.get("status") not in {"draft", "ready"}:
        error("Invalid pack status")
    if m.get("kind") not in {"writing", "demo"}:
        error("Invalid pack kind")
    if m.get("source_mode") != "bundled":
        error("This validator supports bundled portable packs only")
    if type(m.get("revision")) is not int or m["revision"] < 1:
        error("revision must be a positive integer")
    aliases = m.get("aliases")
    if not isinstance(aliases, list) or any(not isinstance(x, str) or not x.strip() for x in aliases):
        error("aliases must be a list of nonempty strings")
    complete = m.get("status") == "ready" or not allow_draft
    if not allow_draft and m.get("status") != "ready":
        error("Pack is draft; semantic distillation is not complete")
    sources = load_rows("corpus.jsonl", True)
    by_source = {}
    texts = {}
    if not sources:
        error("No source units")
    source_files = set()
    for s in sources:
        sid = s.get("id")
        if not isinstance(sid, str) or not re.fullmatch(r"C\d{4,}", sid):
            error(f"Invalid source id: {sid}")
            continue
        if sid in by_source:
            error(f"Duplicate source id: {sid}")
        by_source[sid] = s
        try:
            path = within(pack, s.get("file"))
            if not s["file"].startswith("corpus/"):
                error(f"{sid}: source must be in corpus/")
            if path in source_files:
                error(f"{sid}: duplicate source path")
            source_files.add(path)
            data = path.read_bytes()
            if digest(data) != s.get("sha256"):
                error(f"{sid}: source hash mismatch")
            text = data.decode(s["encoding"])
            texts[sid] = text.splitlines()
            if len(texts[sid]) != s.get("line_count"):
                error(f"{sid}: line_count mismatch")
            if len(re.sub(r"\s", "", text)) != s.get("chars_no_space"):
                error(f"{sid}: chars_no_space mismatch")
        except (OSError, ValueError, KeyError, TypeError, LookupError) as exc:
            error(f"{sid}: {exc}")
    def span_check(span, label, quote=False):
        if not isinstance(span, dict):
            error(f"{label}: invalid span")
            return
        sid = span.get("source_id")
        if sid not in texts:
            error(f"{label}: unknown or unreadable source {sid}")
            return
        a, b = span.get("start_line"), span.get("end_line")
        if type(a) is not int or type(b) is not int or not (1 <= a <= b <= len(texts[sid])):
            error(f"{label}: invalid line range")
            return
        if quote:
            q = span.get("quote")
            if not isinstance(q, str) or not q.strip() or q not in "\n".join(texts[sid][a-1:b]):
                error(f"{label}: quote is not exact within the cited lines")
    rules = load_rows("rules.jsonl", complete)
    rule_ids = set()
    rule_dimensions = {}
    for rule in rules:
        rid = rule.get("id")
        if not isinstance(rid, str) or not re.fullmatch(r"R\d{3,}", rid):
            error(f"Invalid rule id: {rid}")
            continue
        if rid in rule_ids:
            error(f"Duplicate rule id: {rid}")
        rule_ids.add(rid)
        rule_dimensions[rid] = rule.get("dimension")
        for key in ("dimension", "claim", "when", "do", "effect", "avoid"):
            if not isinstance(rule.get(key), str) or not rule[key].strip():
                error(f"{rid}: missing {key}")
        if rule.get("strength") not in {"repeated", "local", "tentative"}:
            error(f"{rid}: invalid strength")
        for key in ("evidence", "counterevidence"):
            spans = rule.get(key)
            if not isinstance(spans, list) or (key == "evidence" and not spans):
                error(f"{rid}: invalid {key}")
            else:
                for span in spans:
                    span_check(span, f"{rid}.{key}", quote=True)
    analysis = load_rows("analysis.jsonl", complete)
    analyzed = set()
    for a in analysis:
        sid = a.get("source_id")
        if sid not in by_source:
            error(f"analysis: unknown source {sid}")
            continue
        if sid in analyzed:
            error(f"analysis: duplicate record {sid}")
        analyzed.add(sid)
        if a.get("sha256") != by_source[sid].get("sha256"):
            error(f"{sid}: stale analysis hash")
        if not isinstance(a.get("observation"), str) or not a["observation"].strip():
            error(f"{sid}: missing observation")
        if not isinstance(a.get("unresolved"), list) or any(not isinstance(x, str) for x in a["unresolved"]):
            error(f"{sid}: unresolved must be a string list")
        refs = a.get("rule_ids")
        if not isinstance(refs, list) or any(x not in rule_ids for x in refs):
            error(f"{sid}: invalid rule_ids")
    if complete and analyzed != set(by_source):
        error("Unanalyzed inputs: " + ", ".join(sorted(set(by_source)-analyzed)))
    scenes = load_rows("scenes.jsonl", complete)
    scene_ids = set()
    for s in scenes:
        sid = s.get("id")
        if not isinstance(sid, str) or sid in scene_ids:
            error(f"Invalid or duplicate scene id: {sid}")
        scene_ids.add(sid)
        if not isinstance(s.get("summary"), str) or not s["summary"].strip():
            error(f"{sid}: missing scene summary")
        if not isinstance(s.get("tags"), list) or not s["tags"] or any(not isinstance(x, str) for x in s["tags"]):
            error(f"{sid}: tags must be a nonempty string list")
        spans = s.get("spans")
        if not isinstance(spans, list) or not spans:
            error(f"{sid}: missing spans")
        else:
            for span in spans:
                span_check(span, sid)
    coverage_path = within(pack, "coverage.json")
    coverage = load_json("coverage.json", {}) if coverage_path.exists() or complete else {}
    dimensions = coverage.get("dimensions", []) if isinstance(coverage, dict) else []
    covered = set()
    covered_rules = set()
    if not isinstance(dimensions, list):
        error("coverage.dimensions must be a list")
        dimensions = []
    for d in dimensions:
        if not isinstance(d, dict):
            error("Invalid coverage entry")
            continue
        dim, state = d.get("dimension"), d.get("status")
        if not isinstance(dim, str) or not dim.strip() or dim in covered:
            error(f"Invalid or duplicate coverage dimension: {dim}")
            continue
        covered.add(dim)
        refs = d.get("rule_ids")
        if not isinstance(refs, list) or any(x not in rule_ids for x in refs):
            error(f"{dim}: invalid coverage rule_ids")
            refs = []
        covered_rules.update(refs)
        if state not in {"observed", "partial", "unobserved", "not_applicable"}:
            error(f"{dim}: invalid coverage status")
        if state in {"unobserved", "not_applicable"} and refs:
            error(f"{dim}: unobserved dimension has rules")
        if state in {"observed", "partial"} and not refs:
            error(f"{dim}: observed dimension has no rules")
        if any(rule_dimensions.get(r) != dim for r in refs):
            error(f"{dim}: rule belongs to a different dimension")
        if not isinstance(d.get("note"), str) or not d["note"].strip():
            error(f"{dim}: missing coverage note")
        if not isinstance(d.get("suggested_inputs"), list) or any(not isinstance(x, str) for x in d["suggested_inputs"]):
            error(f"{dim}: suggested_inputs must be a string list")
    if complete:
        if not set(DIMENSIONS).issubset(covered):
            error("Coverage is missing base dimensions")
        if covered_rules != rule_ids:
            error("Coverage does not account for every rule")
    modules = m.get("modules")
    if not isinstance(modules, list):
        error("manifest.modules must be a list")
        modules = []
    declared_files = ["PACK.md", "缺口与补充建议.md"]
    for module in modules:
        if not isinstance(module, dict):
            error("Invalid module declaration")
            continue
        declared_files.append(module.get("file"))
        dims = module.get("dimensions")
        if not isinstance(dims, list) or not dims or any(d not in covered for d in dims):
            error("Module references an unknown dimension")
    if complete and rules and not modules:
        error("Rules exist but no readable fingerprint module is declared")
    warnings = []
    for s in scenes:
        sid = s.get("id")
        for span in s.get("spans") or []:
            if isinstance(span, dict) and span.get("source_id") in texts:
                total_lines = len(texts[span["source_id"]])
                a, b = span.get("start_line"), span.get("end_line")
                if (isinstance(a, int) and isinstance(b, int)
                        and total_lines >= 50 and (b - a + 1) >= int(total_lines * 0.9)):
                    warnings.append(
                        f"{sid}: spans {b - a + 1}/{total_lines} lines of {span['source_id']}; "
                        "prefer paragraph-level golden scene slicing over whole-chapter spans"
                    )
    for relative in declared_files:
        try:
            p = within(pack, relative)
            if not p.is_file():
                if complete:
                    error(f"Missing module or entry: {relative}")
                continue
            text = p.read_text(encoding="utf-8-sig")
            if not text.strip():
                error(f"Empty module: {relative}")
            elif relative.startswith("指纹/") and len(rules) >= 10 and len(re.sub(r"\s", "", text)) < 300:
                warnings.append(f"{relative}: module is under 300 chars; include formulas, beat templates and examples")
            for rid in re.findall(r"\bR\d{3,}\b", text):
                if rid not in rule_ids:
                    error(f"{relative}: unknown rule reference {rid}")
        except (OSError, ValueError, TypeError) as exc:
            error(str(exc))
    return {"status": "FAIL" if errors else "PASS", "pack_status": m.get("status"),
            "source_units": len(sources), "analyzed_units": len(analyzed),
            "rules": len(rules), "scenes": len(scenes), "errors": errors,
            "warnings": warnings,
            "limits": "Checks records and exact evidence, not semantic truth, plot fidelity or literary quality"}


def slice_scenes(pack, scene_ids=None, tag=None, limit=5):
    sources = {s["id"]: s for s in read_rows(within(pack, "corpus.jsonl"))}
    scenes = read_rows(within(pack, "scenes.jsonl"))
    wanted_ids = set(scene_ids or [])
    matched = []
    for scene in scenes:
        if wanted_ids and scene.get("id") not in wanted_ids:
            continue
        if tag and tag not in (scene.get("tags") or []):
            continue
        matched.append(scene)
        if not wanted_ids and len(matched) >= max(1, limit):
            break
    results = []
    for scene in matched:
        excerpts = []
        for span in scene.get("spans") or []:
            src = sources.get(span["source_id"])
            if not src:
                continue
            lines = within(pack, src["file"]).read_bytes().decode(src["encoding"]).splitlines()
            a, b = span["start_line"], span["end_line"]
            excerpts.append({
                "source_id": src["id"],
                "input_name": src["input_name"],
                "start_line": a,
                "end_line": b,
                "text": "\n".join(lines[a - 1:b]),
            })
        results.append({
            "id": scene["id"],
            "tags": scene.get("tags", []),
            "summary": scene.get("summary", ""),
            "excerpts": excerpts,
        })
    return {"count": len(results), "scenes": results}


def find_packs(root, name):
    def norm(s):
        return unicodedata.normalize("NFKC", s).strip().casefold()
    matches = []
    invalid = []
    for p in sorted(root.rglob("manifest.json")) if root.exists() else []:
        try:
            m = read_json(p)
            if not isinstance(m, dict) or m.get("type") != "novel-writing-pack":
                continue
            aliases = m.get("aliases", [])
            names = [m.get("name", "")] + (aliases if isinstance(aliases, list) else [])
            if any(isinstance(n, str) and norm(n) == norm(name) for n in names):
                matches.append({"path": str(p.parent.resolve()), "name": m.get("name"),
                                "kind": m.get("kind"), "status": m.get("status"),
                                "scope": m.get("scope")})
        except (OSError, ValueError) as exc:
            invalid.append({"path": str(p), "error": str(exc)})
    return {"status": "found" if len(matches) == 1 else "ambiguous" if matches else "not_found",
            "matches": matches, "invalid_manifests": invalid}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "add"):
        p = sub.add_parser(command)
        p.add_argument("--input", nargs="+", required=True)
        p.add_argument("--recursive", action="store_true", help="include nested source folders")
        if command == "init":
            p.add_argument("--name", required=True)
            p.add_argument("--output", required=True)
            p.add_argument("--alias", action="append")
            p.add_argument("--kind", choices=("writing", "demo"), default="writing")
        else:
            p.add_argument("--pack", required=True)
    for command in ("stats", "validate"):
        p = sub.add_parser(command)
        p.add_argument("--pack", required=True)
        if command == "validate":
            p.add_argument("--allow-draft", action="store_true")
    p = sub.add_parser("find")
    p.add_argument("--root", required=True)
    p.add_argument("--name", required=True)
    p = sub.add_parser("slice")
    p.add_argument("--pack", required=True)
    p.add_argument("--scene", nargs="*", help="explicit scene IDs, e.g. S001 S004")
    p.add_argument("--tag", help="filter scenes by tag")
    p.add_argument("--limit", type=int, default=5, help="max scenes when filtering by tag")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = init_pack(args)
        elif args.command == "add":
            result = add_sources(args)
        elif args.command == "stats":
            result = stats_for_pack(Path(args.pack).resolve())
        elif args.command == "validate":
            result = validate_pack(Path(args.pack).resolve(), args.allow_draft)
        elif args.command == "slice":
            result = slice_scenes(Path(args.pack).resolve(), args.scene, args.tag, args.limit)
        else:
            result = find_packs(Path(args.root).resolve(), args.name)
        emit(result)
        return 1 if result.get("status") == "FAIL" else 2 if result.get("status") in {"ambiguous", "not_found"} else 0
    except (OSError, ValueError, KeyError, TypeError, LookupError) as exc:
        emit({"status": "FAIL", "error": str(exc)})
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
