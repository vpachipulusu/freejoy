"""Pre-commit and gate check: is docs/partner/openapi.json up to date with the partner sub-app?

Regenerates the partner OpenAPI schema into a temporary file and compares it with the committed
copy. Exits 1 (with a hint) when they differ or the file is missing. The internal schema is
checked separately by ``commands.check_openapi_schema``.
"""

import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCHEMA_FILE = BACKEND_DIR.parent / "docs" / "partner" / "openapi.json"
REGENERATE = "  uv run python -m commands.generate_partner_openapi_schema"


def main() -> int:
    if not SCHEMA_FILE.exists():
        print(
            "partner-openapi-schema-check: docs/partner/openapi.json is missing.\n"
            "Generate it from fastapi_backend/ with:\n" + REGENERATE
        )
        return 1

    # Importing the generator isolates the environment (placeholders, no .env) before the app loads.
    from commands import generate_partner_openapi_schema as generator

    generate: Callable[[Path], None] = generator.generate_partner_openapi_schema

    with tempfile.TemporaryDirectory() as tmp_dir:
        fresh_file = Path(tmp_dir) / "openapi.json"
        generate(fresh_file)
        fresh = json.loads(fresh_file.read_text(encoding="utf-8"))

    committed = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    if fresh != committed:
        print(
            "partner-openapi-schema-check: docs/partner/openapi.json is out of date.\n"
            "Regenerate it from fastapi_backend/ with:\n" + REGENERATE
        )
        return 1

    print("partner-openapi-schema-check: docs/partner/openapi.json is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
