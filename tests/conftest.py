"""Shared pytest fixtures.

Qt objects need a QApplication and an offscreen platform plugin. Creating one
per test module duplicated boilerplate and previously let a plain
QCoreApplication clash with a QApplication (a hard crash, exit code 9,
depending on module collection order). Create exactly one, once, here.
"""
import os

# Must be set before any QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """One QApplication for the whole test session."""
    app = QApplication.instance() or QApplication([])
    yield app
