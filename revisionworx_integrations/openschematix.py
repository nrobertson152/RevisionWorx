"""Read-only, stdlib-only OpenSchematix indexer; does not launch the CAD GUI.

Contract: inspect(path) returns a JSON-serializable report, never modifies path.
Compatibility is intentionally limited to known project container v1 and
legacy single-sheet schema 1..3. BOM output is *candidate* data, not a
manufacturing BOM, and is not claimed equivalent to OpenSchematix bom.generate.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import stat
from pathlib import Path
import zipfile

MAX_SOURCE = 256 * 1024 * 1024
MAX_MEMBER = 200 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_MEMBERS = 100_000
MAX_INSPECTED_MEMBERS = 1000
MAX_DEPTH = 64
MIMETYPE = b"application/x-openschematix-project"


class UnsupportedFormat(ValueError):
    pass


class InvalidProject(ValueError):
    pass


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise InvalidProject("non-finite numeric field")
    return result


def _integer(value: str) -> int:
    if len(value.lstrip("-")) > 18:
        raise InvalidProject("oversized integer")
    return int(value)


def _reject_constant(value: str):
    raise InvalidProject("non-standard JSON numeric constant")


def _json(raw: bytes, where: str) -> dict:
    try:
        result = json.loads(raw.decode("utf-8"), parse_int=_integer,
                            parse_float=_finite_float, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise InvalidProject(f"invalid JSON in {where}: {type(exc).__name__}") from exc
    stack = [(result, 1)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, (dict, list)):
            if depth > MAX_DEPTH:
                raise InvalidProject(f"excessive JSON nesting in {where}")
            stack.extend((v, depth + 1) for v in
                         (value.values() if isinstance(value, dict) else value))
    if not isinstance(result, dict):
        raise InvalidProject(f"{where} must contain an object")
    return result


def _version(value, maximum: int, label: str) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise UnsupportedFormat(f"unsupported {label}: {value!r}")
    return value


def _safe_path(value: str, directory: str) -> str:
    if (not isinstance(value, str) or not value.startswith(directory + "/")
            or "\\" in value or ":" in value or ".." in value.split("/")
            or value.startswith("/") or value.endswith("/")):
        raise InvalidProject(f"unsafe {directory} member path")
    return value


def _components(sheets: list[dict], project_uid: str, revision: str) -> tuple[list[dict], list[dict]]:
    components = []
    edges = []
    for sheet in sheets:
        uid = sheet["uid"]
        seen = set()
        for comp in sheet.get("components", []):
            if not isinstance(comp, dict) or type(comp.get("id")) is not int or comp["id"] < 0:
                raise InvalidProject("invalid component id")
            if comp["id"] in seen:
                raise InvalidProject("duplicate component id within sheet")
            seen.add(comp["id"])
            if not isinstance(comp.get("symbol"), str) or not comp["symbol"]:
                raise InvalidProject("missing component symbol")
            for axis in ("x", "y"):
                if axis not in comp or type(comp[axis]) not in (int, float) or not math.isfinite(comp[axis]):
                    raise InvalidProject("invalid component coordinate")
            props = comp.get("props", {})
            if not isinstance(props, dict):
                raise InvalidProject("component props must be a mapping")
            if not isinstance(comp.get("params", {}), dict):
                raise InvalidProject("component params must be a mapping")
            for p in ("part_number", "manufacturer", "description", "rating"):
                if p in props and not isinstance(props[p], str):
                    raise InvalidProject("invalid component property")
            ident = f"{project_uid}/{uid}/{comp['id']}"
            part_number = props.get("part_number", "").strip()
            manufacturer = props.get("manufacturer", "").strip()
            item = {
                "instance_id": ident, "sheet_uid": uid, "object_id": comp["id"],
                "symbol_id": comp["symbol"], "designator": comp.get("designator", ""),
                "label": comp.get("label", ""), "in_bom": comp.get("in_bom", True),
                "manufacturer": manufacturer, "manufacturer_part_number": part_number,
                "description": props.get("description", ""),
                "rating": props.get("rating", ""),
                "properties": props.copy(), "params": comp.get("params", {}).copy(),
                "source_revision": revision,
            }
            components.append(item)
            if part_number and item["in_bom"]:
                edges.append({
                    "source_instance": ident, "target_kind": "manufacturer_part_candidate",
                    "target_key": [manufacturer, part_number], "target_revision": None,
                    "provenance": "openschematix.component.props",
                    "verification": "unresolved", "quantity": None,
                })
    return components, edges


def _sheet(data: dict, fallback_uid: str) -> dict:
    if not isinstance(data.get("components", []), list):
        raise InvalidProject("sheet components must be a list")
    if not isinstance(data.get("wires", []), list):
        raise InvalidProject("sheet wires must be a list")
    uid = data.get("uid", fallback_uid)
    if not isinstance(uid, str) or not uid:
        raise InvalidProject("invalid sheet UID")
    sheet_metadata = data.get("sheet", {})
    if not isinstance(sheet_metadata, dict):
        raise InvalidProject("invalid sheet metadata")
    return {
        "uid": uid, "name": data.get("name", "Sheet 1"),
        "metadata": sheet_metadata.copy(),
        "components": data.get("components", []),
        "wire_count": len(data.get("wires", [])),
    }


def inspect(path: str | Path) -> dict:
    """Index exactly one explicitly chosen file; never browse neighboring files."""
    path = Path(path)
    # Reject links before following their targets, mirroring document registration.
    mode = os.lstat(path).st_mode
    if not stat.S_ISREG(mode):
        raise InvalidProject("source must be a regular file, not a link or device")
    # The immutable snapshot is hashed and parsed exactly once. Detect edits
    # during capture rather than reporting a possibly torn source as current.
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise InvalidProject("source must be a regular file")
        raw = handle.read(MAX_SOURCE + 1)
        after = os.fstat(handle.fileno())
    if len(raw) > MAX_SOURCE:
        raise InvalidProject("source file exceeds size limit")
    if (before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns or len(raw) != after.st_size):
        raise InvalidProject("source changed during inspection")
    digest = hashlib.sha256(raw)
    if path.suffix.lower() == ".osch":
        if len(raw) > MAX_MEMBER:
            raise InvalidProject("legacy file exceeds size limit")
        data = _json(raw, "legacy drawing")
        if data.get("format") != "OpenSchematix":
            raise UnsupportedFormat("not an OpenSchematix drawing")
        schema = _version(data.get("schema", 1), 3, "legacy sheet schema")
        sheet = _sheet(data, f"legacy:{digest.hexdigest()[:16]}")
        # Legacy sheets have no persistent sheet UID; content hash is not a stable identity.
        uid = None
        metadata = {"title": sheet["metadata"].get("title", ""),
                    "revision": sheet["metadata"].get("revision", ""),
                    "number": sheet["metadata"].get("drawing_number", "")}
        sheets = [sheet]
        fmt = "osch"
        manifest_version = None
        warnings = ["Legacy sheet identity is content-derived and changes on edit."]
    elif path.suffix.lower() == ".schematix":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_TOTAL:
                    raise InvalidProject("ZIP size/member count limit exceeded")
                names = [i.filename for i in infos]
                if len(names) != len(set(names)):
                    raise InvalidProject("ambiguous duplicate ZIP member names")
                for info in infos:
                    n = info.filename
                    # Treat all members as untrusted, including ignored symbols/revisions.
                    if (not n or n.startswith("/") or "\\" in n or ":" in n
                            or any(p in ("", ".", "..") for p in n.split("/"))
                            or info.file_size < 0 or info.compress_size < 0):
                        raise InvalidProject("unsafe ZIP member path or size")
                if len(infos) > MAX_INSPECTED_MEMBERS:
                    raise InvalidProject("ZIP exceeds inspection member limit")
                if not infos or infos[0].filename != "mimetype" or infos[0].compress_type != zipfile.ZIP_STORED:
                    raise InvalidProject("missing first uncompressed mimetype member")
                if infos[0].file_size != len(MIMETYPE) or archive.read("mimetype") != MIMETYPE:
                    raise UnsupportedFormat("unrecognized project MIME type")
                def member(name):
                    if name not in names or archive.getinfo(name).file_size > MAX_MEMBER:
                        raise InvalidProject(f"missing or oversized member: {name}")
                    with archive.open(name) as stream:
                        content = stream.read(MAX_MEMBER + 1)
                    if len(content) > MAX_MEMBER:
                        raise InvalidProject(f"decompression limit exceeded: {name}")
                    return _json(content, name)
                manifest = member("manifest.json")
                if manifest.get("format") != "OpenSchematix Project":
                    raise UnsupportedFormat("unrecognized manifest format")
                manifest_version = _version(manifest.get("version"), 1, "container version")
                schema = _version(manifest.get("sheet_schema", 2), 3, "sheet schema")
                head = member("project.json")
                uid = head.get("uid") or manifest.get("project_uid")
                if not isinstance(uid, str) or not uid:
                    raise InvalidProject("missing project UID")
                metadata = head.get("meta", {})
                if not isinstance(metadata, dict):
                    raise InvalidProject("invalid project metadata")
                entries = manifest.get("sheets")
                if not isinstance(entries, list):
                    raise InvalidProject("invalid manifest sheets")
                if len(entries) > MAX_INSPECTED_MEMBERS:
                    raise InvalidProject("too many sheets")
                sheets = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise InvalidProject("invalid sheet entry")
                    name = _safe_path(entry.get("path"), "sheets")
                    doc = _sheet(member(name), entry.get("uid", ""))
                    if doc["uid"] != entry.get("uid"):
                        raise InvalidProject("manifest/sheet UID mismatch")
                    sheets.append(doc)
                if len(set(s["uid"] for s in sheets)) != len(sheets):
                    raise InvalidProject("duplicate sheet UIDs")
                order = head.get("sheet_order", [])
                if order:
                    if not isinstance(order, list) or len(order) != len(set(order)):
                        raise InvalidProject("invalid sheet ordering")
                    ranking = {key: i for i, key in enumerate(order)}
                    sheets.sort(key=lambda s: ranking.get(s["uid"], len(ranking)))
                fmt = "schematix"
                warnings = []
                if manifest.get("revisions"):
                    warnings.append("Internal revision snapshots not indexed; use RevisionWorx immutable file revisions.")
                if manifest.get("symbols"):
                    warnings.append("Embedded custom symbols not interpreted; symbol-specific BOM classification unavailable.")
        except (zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError, OSError) as exc:
            raise InvalidProject(f"damaged ZIP container: {type(exc).__name__}") from exc
    else:
        raise UnsupportedFormat("supported extensions: .schematix and .osch")
    components, dependencies = _components(sheets, uid or f"legacy:{digest.hexdigest()}", metadata.get("revision", ""))
    return {
        "adapter": "openschematix-readonly-v1", "source_format": fmt,
        "container_version": manifest_version, "sheet_schema": schema,
        "file_sha256": digest.hexdigest(), "project_uid": uid,
        "project_metadata": metadata, "sheets": [
            {"uid": s["uid"], "name": s["name"], "metadata": s["metadata"],
             "component_count": len(s["components"]), "wire_count": s["wire_count"]} for s in sheets
        ], "components": components, "dependencies": dependencies,
        "bom_candidates": _candidate_bom(components),
        "bom_status": "candidate_only_not_equivalent_to_native_bom",
        "coverage": {"indexed_sheets": len(sheets), "indexed_components": len(components),
                     "external_projects": "not_scanned", "symbol_libraries": "not_resolved"},
        "warnings": warnings,
    }


def _candidate_bom(components: list[dict]) -> list[dict]:
    """One record per explicit manufacturer/part number, no guessed quantities.

    The native OpenSchematix BOM merges multi-unit devices, and excludes certain
    symbol types; these operations require native symbol metadata. A heuristic
    count would be materially incorrect, so quantity remains unknown.
    """
    groups = {}
    for c in components:
        if not c["in_bom"] or not c["manufacturer_part_number"]:
            continue
        key = (c["manufacturer"].casefold(), c["manufacturer_part_number"].casefold())
        item = groups.setdefault(key, {"manufacturer": c["manufacturer"],
                                       "manufacturer_part_number": c["manufacturer_part_number"],
                                       "quantity": None, "verification": "unresolved",
                                       "source_instances": []})
        item["source_instances"].append(c["instance_id"])
    return list(groups.values())
