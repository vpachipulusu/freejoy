"""Write the partner OpenAPI document (ADR-013) to ``docs/partner/openapi.json``.

This is the retailer-facing contract. It is a separate file from ``nextjs-frontend/openapi.json``
(the internal contract the generated client is built from), which must never contain a
``/partner`` path. It lives under ``docs/`` because nothing in the frontend consumes it and
the contract is what gets shared with retailers. Every path is written as it is called
(``/partner/v1/...``).

The same bytes are also written to ``nextjs-frontend/public/partner-openapi.json``, the copy the public
documentation page offers for download (FREEJ-59). The default run writes both; the frontend test and
``commands.check_partner_openapi_schema`` fail when they differ.
"""

import json
import os
from pathlib import Path

from app.core.settings_environment import isolate_settings_environment

DEFAULT_OUTPUT_FILE = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "partner" / "openapi.json"
)
OUTPUT_FILE = os.getenv("PARTNER_OPENAPI_OUTPUT_FILE") or DEFAULT_OUTPUT_FILE
PUBLIC_COPY_FILE = (
    Path(__file__).resolve().parent.parent.parent
    / "nextjs-frontend"
    / "public"
    / "partner-openapi.json"
)


def generate_partner_openapi_schema(
    output_file: str | Path, *copies: str | Path
) -> None:
    # Same reasoning as commands.generate_openapi_schema: the committed file must not depend on one
    # developer's machine, and the isolation has to run before anything imports the settings.
    isolate_settings_environment(os.environ)
    from app.partner.app import create_partner_api
    from app.partner.schema import partner_openapi_schema

    schema = partner_openapi_schema(create_partner_api())
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # LF newlines: the committed schema must be byte-identical on Windows and CI.
    output_path.write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Partner OpenAPI schema saved to {output_file}")
    for copy in map(Path, copies):
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(output_path.read_bytes())
        print(f"Partner OpenAPI schema copied to {copy}")


if __name__ == "__main__":
    # An overridden output file (a test, a one-off) does not touch the committed public copy.
    generate_partner_openapi_schema(
        OUTPUT_FILE, *([PUBLIC_COPY_FILE] if OUTPUT_FILE == DEFAULT_OUTPUT_FILE else [])
    )
