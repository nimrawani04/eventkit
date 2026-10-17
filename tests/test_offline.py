import socket
import subprocess

import pytest


def test_offline_guard_blocks_network_and_subprocess_access():
    with pytest.raises(RuntimeError, match="forbidden"):
        socket.socket()

    with pytest.raises(RuntimeError, match="forbidden"):
        subprocess.Popen(["would-not-run"])
