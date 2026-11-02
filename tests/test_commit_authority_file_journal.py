# SPDX-License-Identifier: GPL-3.0-or-later
"""The file journal (standalone mode) against the CommitAuthority contract."""
import pytest

from contract_commit_authority import CommitAuthorityContract, PreconditionIdentityContract
from revisionworx.workspace import Workspace


@pytest.fixture
def authority(share):
    ws = Workspace.create(share, "Contract")
    yield ws
    ws._lock_token = ws._lock_token if ws.writable else None
    ws.close()


@pytest.fixture
def revoke(share):
    def _revoke(ws):
        Workspace.break_writer_lock(share, ws._lock_token, reason="contract test")
    return _revoke


class TestFileJournal(CommitAuthorityContract, PreconditionIdentityContract):
    pass
