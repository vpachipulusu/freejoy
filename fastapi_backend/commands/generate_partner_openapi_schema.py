"""Write the partner OpenAPI document (ADR-013) to ``docs/partner/openapi.json``.

This is the retailer-facing contract. It is a separate file from ``nextjs-frontend/openapi.json``
(the internal contract the generated client is built from), which must never contain a
``/partner`` path. It lives under ``docs/`` because nothing in the frontend consumes it and
the contract is what gets shared with retailers. Every path is written as it is called
(``/partner/v1/...``). With no endpoints yet the document has no paths; the command and its
check still work, and mean something as soon as the first endpoint exists.
"""

import json
import os
from pathlib import Path

from app.core.settings_environment import isolate_settings_environment

DEFAULT_OUTPUT_FILE = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "partner" / "openapi.json"
)
OUTPUT_FILE = os.getenv("PARTNER_OPENAPI_OUTPUT_FILE") or DEFAULT_OUTPUT_FILE


def generate_partner_openapi_schema(output_file: str | Path) -> None:
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


if __name__ == "__main__":
    generate_partner_openapi_schema(OUTPUT_FILE)
