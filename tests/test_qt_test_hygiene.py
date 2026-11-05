# SPDX-License-Identifier: GPL-3.0-or-later
"""The suite's own Qt hygiene (tests/conftest.py): windows a Qt test closed are deleted afterwards, so later theme
changes do not re-style hundreds of hidden windows, and nothing a test still uses is touched."""
import pytest

pytestmark = pytest.mark.qt
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_only_hidden_parentless_windows_made_in_the_block_are_deleted(qapp):
    import shiboken6
    from conftest import qt_window_cleanup
    older = QtWidgets.QWidget()                      # hidden, but made before the block: kept
    with qt_window_cleanup():
        closed = QtWidgets.QWidget()
        closed.show()
        closed.close()
        never_shown = QtWidgets.QMainWindow()
        shown = QtWidgets.QWidget()
        shown.show()
        child = QtWidgets.QDialog(shown)             # a window with a parent goes with its parent, not before
    assert not shiboken6.isValid(closed) and not shiboken6.isValid(never_shown)
    assert shiboken6.isValid(older) and shiboken6.isValid(shown) and shiboken6.isValid(child)
    shown.close()
    older.deleteLater()
