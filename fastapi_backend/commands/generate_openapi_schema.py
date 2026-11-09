import json
import os
from pathlib import Path
from typing import Any

from app.core.env_file import IGNORE_ENV_FILE

# The schema is committed (ADR-008), so it must not depend on one developer's machine: a
# local .env or an exported APP_NAME/APP_VERSION would change info.title and info.version.
# This runs before app.main builds the settings and the app.
os.environ[IGNORE_ENV_FILE] = "1"
for _brand_setting in ("APP_NAME", "APP_VERSION"):
    os.environ.pop(_brand_setting, None)

from app.main import app

# Defaults to the frontend schema that the generated TypeScript client is built from
# (ADR-008); set OPENAPI_OUTPUT_FILE to write elsewhere.
DEFAULT_OUTPUT_FILE = (
    Path(__file__).resolve().parent.parent.parent / "nextjs-frontend" / "openapi.json"
)
OUTPUT_FILE = os.getenv("OPENAPI_OUTPUT_FILE") or DEFAULT_OUTPUT_FILE


def generate_openapi_schema(output_file: str | Path) -> None:
    schema = app.openapi()
    output_path = Path(output_file)

    updated_schema = remove_operation_id_tag(schema)

    # LF newlines: the committed schema must be byte-identical on Windows and CI.
    output_path.write_text(
        json.dumps(updated_schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"OpenAPI schema saved to {output_file}")


def remove_operation_id_tag(schema: dict[str, Any]) -> dict[str, Any]:
    """
    Removes the tag prefix from the operation IDs in the OpenAPI schema.

    This cleans up the OpenAPI operation IDs that are used by the frontend
    client generator to create the names of the functions. The modified
    schema is then returned.
    """
    for path_data in schema["paths"].values():
        for operation in path_data.values():
            tag = operation["tags"][0]
            operation_id = operation["operationId"]
            to_remove = f"{tag}-"
            new_operation_id = operation_id[len(to_remove) :]
            operation["operationId"] = new_operation_id
    return schema


if __name__ == "__main__":
    generate_openapi_schema(OUTPUT_FILE)
