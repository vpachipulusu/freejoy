"""Run ``commands.import_awin_offers`` in a child process with a fake Awin client and no network.

Used by ``test_import_awin_offers_e2e.py``. Before anything else is imported this module makes every
outbound connection except to the loopback interface fail, so even a bug that built the real client
could not reach Awin (or anything else): the only socket the process may open is the one to the test
database. The real client is also replaced by a function that fails, so that bug would be loud.

    python -m tests.commands.awin_cli_runner probe
    python -m tests.commands.awin_cli_runner run <items> <title suffix or -> [command arguments]
"""

import asyncio
import socket
import sys
from typing import Any

LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
BLOCKED = "network access is blocked in this test process"


def block_network() -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def allowed(address: Any) -> bool:
        return isinstance(address, tuple) and address[0] in LOOPBACK

    def connect(self: socket.socket, address: Any) -> None:
        if not allowed(address):
            raise OSError(BLOCKED)
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if not allowed(address):
            raise OSError(BLOCKED)
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None and host not in LOOPBACK:
            raise socket.gaierror(BLOCKED)
        return real_getaddrinfo(host, *args, **kwargs)

    _block_event_loop_connections(allowed)
    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]


def _block_event_loop_connections(allowed: Any) -> None:
    """asyncio's Windows loop connects through the OS, not ``socket.connect``: guard both loop types."""
    from asyncio import proactor_events, selector_events

    for loop_class in (
        getattr(selector_events, "BaseSelectorEventLoop", None),
        getattr(proactor_events, "BaseProactorEventLoop", None),
    ):
        if loop_class is None or not hasattr(loop_class, "sock_connect"):
            continue
        original = loop_class.sock_connect

        async def sock_connect(
            self: Any, sock: Any, address: Any, _original: Any = original
        ) -> None:
            if not allowed(address):
                raise OSError(BLOCKED)
            await _original(self, sock, address)

        loop_class.sock_connect = sock_connect


async def _async_probe() -> bool:
    try:
        await asyncio.wait_for(asyncio.open_connection("93.184.216.34", 443), timeout=2)
    except OSError:
        return True
    return False


def probe() -> int:
    """0 only when a connection to a public address and a lookup of a public name both fail."""
    try:
        socket.create_connection(("93.184.216.34", 443), timeout=2)
    except OSError:
        pass
    else:
        return 1
    if not asyncio.run(_async_probe()):
        return 1
    try:
        socket.getaddrinfo("api.awin.com", 443)
    except OSError:
        print("blocked")
        return 0
    return 1


def run(argv: list[str]) -> int:
    from commands import import_awin_offers as command
    from tests.awin_samples import PUBLISHER_ID, raw_item
    from tests.fakes_awin import FakeAwinClient

    def refuse(*_: object) -> None:
        raise AssertionError("the real Awin client must never be built in this test")

    command.build_client = refuse  # type: ignore[assignment]
    count, suffix, *arguments = argv
    items = [
        raw_item(n, title=f"Offer {n}{'' if suffix == '-' else ' ' + suffix}")
        for n in range(1, int(count) + 1)
    ]
    return command.main(
        arguments, client=FakeAwinClient([items]), publisher_id=PUBLISHER_ID
    )


if __name__ == "__main__":
    block_network()
    mode, *rest = sys.argv[1:]
    sys.exit(probe() if mode == "probe" else run(rest))
