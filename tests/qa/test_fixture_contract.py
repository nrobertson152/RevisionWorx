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
