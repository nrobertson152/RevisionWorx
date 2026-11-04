# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line for BOM files: ``python -m revisionworx.bom <command> ...``.

Works on files only (CSV, XLSX or RevisionWorx BOM JSON); it never opens a workspace. Output to a file
ending in ``.xlsx`` is written as a worksheet. Exit status: 0 success,
1 validation errors found, 2 the input could not be read or the command failed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import csvio
from .catalog import number_key
from .diff import compare, compare_rollups
from .errors import BomError, CsvImportError
from .graph import explode, rollup
from .impact import change_impact, revision_impact
from .snapshot import BomSnapshot, load_any, snapshot_to_json
from .validate import validate
from .workspace_adapter import single_root, sniff_layout


def _load(path: str, layout: str, root: str) -> BomSnapshot:
    p = Path(path)
    if p.suffix.casefold() == ".xlsx":
        from . import xlsx
        rows = xlsx.read_rows(p)
        header = " ".join(c.casefold() for c in rows[0][1]) if rows else ""
        if layout == "indented" or (layout == "auto" and ("level" in header or header.startswith("item"))):
            st, r = csvio.structure_from_indented_csv(rows, root_number=root, formula_guard=False)
            return BomSnapshot(st, r, p.stem)
        st = csvio.structure_from_csv(rows, formula_guard=False)
        return BomSnapshot(st, single_root(st, root), p.stem)
    data = csvio.read_file(p, csvio.MAX_CSV_BYTES)
    if layout == "json" or (layout == "auto" and p.suffix.casefold() == ".json"):
        loaded = load_any(data)
        if isinstance(loaded, BomSnapshot):
            return loaded
        return BomSnapshot(loaded, single_root(loaded, root), p.stem)
    if layout == "indented" or (layout == "auto" and sniff_layout(data) == "indented"):
        st, r = csvio.structure_from_indented_csv(data, root_number=root)
        return BomSnapshot(st, r, p.stem)
    st = csvio.structure_from_csv(data)
    return BomSnapshot(st, single_root(st, root), p.stem)


def _emit(data: bytes, out: str | None, overwrite: bool) -> None:
    if out and out.casefold().endswith(".xlsx") and not data.startswith(b"PK"):
        from .xlsx import csv_to_xlsx
        data = csv_to_xlsx(data)
    if out:
        csvio.save(data, out, overwrite=overwrite)
        print(f"wrote {out}", file=sys.stderr)
    else:
        sys.stdout.buffer.write(data)
        sys.stdout.flush()


def _find_item(snap: BomSnapshot, number: str) -> str:
    st = snap.structure
    if number in st.item_ids:
        return number
    matches = [p.part_id for p in st.parts if number_key(p.number) == number_key(number)]
    if len(matches) != 1:
        raise BomError(f"part {number!r} matches {len(matches)} items")
    return matches[0]


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    ap = argparse.ArgumentParser(prog=prog or "python -m revisionworx.bom", description=__doc__.splitlines()[0])
    ap.add_argument("--layout", choices=["auto", "parent-child", "indented", "json"], default="auto")
    ap.add_argument("--root", default="", help="part number of the top-level item when the file has several")
    ap.add_argument("-o", "--output", help="write to this file (all-or-nothing) instead of stdout")
    ap.add_argument("--overwrite", action="store_true", help="allow replacing an existing output file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate").add_argument("file")
    e = sub.add_parser("explode")
    e.add_argument("file")
    e.add_argument("--quantity", default="1")
    r = sub.add_parser("rollup")
    r.add_argument("file")
    r.add_argument("--quantity", default="1")
    r.add_argument("--leaves-only", action="store_true")
    d = sub.add_parser("diff")
    d.add_argument("before")
    d.add_argument("after")
    d.add_argument("--summary", action="store_true", help="summarized quantity changes instead of lines")
    w = sub.add_parser("impact", help="where an item is used across BOM files (Before You Revise)")
    w.add_argument("part")
    w.add_argument("files", nargs="+")
    w.add_argument("--current-revision", default="")
    c = sub.add_parser("change-impact", help="effect of an assembly change on products in context files")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("context", nargs="*")
    pu = sub.add_parser("purchase", help="derived buy list (leaf items, count units rounded up)")
    pu.add_argument("file")
    pu.add_argument("--quantity", default="1")
    v = sub.add_parser("convert", help="convert to parent-child CSV, XLSX or JSON (by output extension)")
    v.add_argument("file")
    args = ap.parse_args(argv)
    from .units import parse_quantity
    try:
        if args.cmd == "validate":
            snap = _load(args.file, args.layout, args.root)
            report = validate(snap.structure, roots=(snap.root_id,))
            _emit(csvio.issues_to_csv(report, excel=False), args.output, args.overwrite)
            return 0 if report.ok else 1
        if args.cmd == "explode":
            snap = _load(args.file, args.layout, args.root)
            ex = explode(snap.structure, snap.root_id, parse_quantity(args.quantity))
            _emit(csvio.explosion_to_csv(ex, snap.structure, excel=False), args.output, args.overwrite)
        elif args.cmd == "rollup":
            snap = _load(args.file, args.layout, args.root)
            ro = rollup(snap.structure, snap.root_id, parse_quantity(args.quantity), leaves_only=args.leaves_only)
            _emit(csvio.rollup_to_csv(ro, snap.structure, excel=False), args.output, args.overwrite)
        elif args.cmd == "diff":
            a, b = _load(args.before, args.layout, args.root), _load(args.after, args.layout, args.root)
            if args.summary:
                deltas = compare_rollups(a.structure, a.root_id, b.structure, b.root_id)
                num = lambda i: (b.structure.part(i) or a.structure.info(i)).display_number  # noqa: E731
                _emit(csvio.quantity_deltas_to_csv(deltas, num, excel=False), args.output, args.overwrite)
            else:
                _emit(csvio.diff_to_csv(compare(a.structure, b.structure), a.structure, b.structure, excel=False),
                      args.output, args.overwrite)
        elif args.cmd == "impact":
            snaps = [_load(f, args.layout, args.root) for f in args.files]
            item = next((_find_item(s, args.part) for s in snaps
                         if any(number_key(p.number) == number_key(args.part) or p.part_id == args.part
                                for p in s.structure.parts)), args.part)
            rep = revision_impact(snaps, item, current_revision=args.current_revision)
            _emit(csvio.revision_impact_to_csv(rep, excel=False), args.output, args.overwrite)
        elif args.cmd == "change-impact":
            a, b = _load(args.before, args.layout, args.root), _load(args.after, args.layout, args.root)
            ctx = [_load(f, args.layout, "") for f in args.context]
            rep = change_impact(a, b, ctx)
            _emit(csvio.change_impact_to_csv(rep, excel=False), args.output, args.overwrite)
        elif args.cmd == "purchase":
            from .purchasing import purchasing_list, purchasing_to_csv
            snap = _load(args.file, args.layout, args.root)
            pl = purchasing_list(snap.structure, snap.root_id, parse_quantity(args.quantity))
            _emit(purchasing_to_csv(pl, excel=False), args.output, args.overwrite)
        elif args.cmd == "convert":
            snap = _load(args.file, args.layout, args.root)
            if args.output and args.output.casefold().endswith(".json"):
                _emit(snapshot_to_json(snap), args.output, args.overwrite)
            else:
                _emit(csvio.structure_to_csv(snap.structure, excel=False), args.output, args.overwrite)
        return 0
    except CsvImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        for p in exc.problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    except (BomError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
