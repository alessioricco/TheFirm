import socket
import urllib.request

import pytest


def test_network_is_blocked_in_tests() -> None:
    with pytest.raises(Exception, match="must not use the network"):
        socket.create_connection(("example.com", 443), timeout=1)
    with pytest.raises(Exception, match="must not use the network"):
        urllib.request.urlopen("https://api.kraken.com/0/public/Time", timeout=1)
