# SPDX-License-Identifier: GPL-3.0-or-later
"""A team workspace against the same CommitAuthority contract as a single-computer one (ADR-017, ADR-024)."""
import pytest

from contract_commit_authority import CommitAuthorityContract, PreconditionIdentityContract, RequestIdentityV2Contract
from revisionworx.shared import SharedWorkspace
from revisionworx.workspace import Workspace
from team_helpers import new_team


@pytest.fixture
def authority(tmp_path):
    folder, boss_pc, _code, session = new_team(tmp_path)
    ws = Workspace.open(folder, writable=True, session=session, state_dir=boss_pc)
    assert isinstance(ws, SharedWorkspace)
    yield ws
    ws.close()


@pytest.fixture
def revoke():
    def _revoke(ws):
        SharedWorkspace.break_lock(ws.meta_dir, ws._lock_token, "contract test", session=ws.session, state_dir=ws.state_dir)
    return _revoke


class TestSharedFolder(CommitAuthorityContract, PreconditionIdentityContract, RequestIdentityV2Contract):
    pass
