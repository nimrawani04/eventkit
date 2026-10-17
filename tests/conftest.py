"""Keep the test suite honest about eventkit's offline contract."""

from __future__ import annotations

import socket
import subprocess

import pytest


def _blocked(*args, **kwargs):
    raise RuntimeError("network and subprocess access are forbidden in eventkit tests")


@pytest.fixture(autouse=True)
def block_external_access(monkeypatch):
    """Prevent tests from accidentally relying on network or subprocesses."""

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(subprocess, "Popen", _blocked)
