# SPDX-License-Identifier: GPL-3.0-or-later
from .fixtures import STRUCTURES, fingerprint, populate

def test_fixture_structures_and_mutation_detection(tmp_path):
    for layout in STRUCTURES:
        fixture = populate(tmp_path / layout, layout)
        assert len(fixture["projects"]) == len(STRUCTURES[layout])
        assert fingerprint(fixture["root"]) == fixture["before"]
        for project in fixture["projects"]:
            assert (project / "Models" / "shared-bracket.ipt").exists()
            assert not (project / "MISSING-COMPONENT.ipt").exists()
        target = fixture["projects"][0] / "Models" / "shared-bracket.ipt"
        target.write_bytes(b"external edit")
        assert fingerprint(fixture["root"]) != fixture["before"]

def test_fingerprint_detects_same_size_content_change_with_restored_mtime(tmp_path):
    """A hash must catch silent CAD edits even if size and timestamp are preserved."""
    root = tmp_path / "Engineering"
    fixture = populate(root, "year")
    target = fixture["projects"][0] / "Models" / "shared-bracket.ipt"
    original_stat = target.stat()
    data = target.read_bytes()
    target.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    import os
    os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert target.stat().st_size == original_stat.st_size
    assert target.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert fingerprint(root) != fixture["before"]


def test_fingerprint_detects_file_deletion_and_rename(tmp_path):
    root = tmp_path / "Engineering"
    fixture = populate(root, "product")
    target = fixture["projects"][0] / "BOM" / "items.csv"
    target.rename(target.with_name("items-renamed.csv"))
    assert fingerprint(root) != fixture["before"]
    target.with_name("items-renamed.csv").unlink()
    assert fingerprint(root) != fixture["before"]
