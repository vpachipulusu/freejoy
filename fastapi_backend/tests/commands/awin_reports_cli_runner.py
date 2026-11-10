"""Run ``commands.import_awin_reports`` in a child process with a fake Awin client and no network.

Used by ``test_import_awin_reports_e2e.py``. Like ``awin_cli_runner`` (whose network block it reuses) every
outbound connection except to the loopback interface fails before anything else is imported, so even a bug
that built the real client could not reach Awin: the only socket the process may open is the one to the test
database. The real client is also replaced by a function that fails, so that bug would be loud.

    python -m tests.commands.awin_reports_cli_runner run <rows> <currency or -> [command arguments]
"""

import sys

from tests.commands.awin_cli_runner import block_network


def run(argv: list[str]) -> int:
    from commands import import_awin_reports as command
    from tests.awin_report_samples import raw_rows
    from tests.fakes_awin import FakeAwinClient

    def refuse(*_: object) -> None:
        raise AssertionError("the real Awin client must never be built in this test")

    command.build_client = refuse  # type: ignore[assignment]
    count, status, *arguments = argv
    rows = raw_rows(
        1, int(count), **({} if status == "-" else {"commissionStatus": status})
    )
    return command.main(arguments, client=FakeAwinClient(transaction_pages=[rows]))


if __name__ == "__main__":
    block_network()
    mode, *rest = sys.argv[1:]
    sys.exit(run(rest))
