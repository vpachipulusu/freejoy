# build-docx

Builds `../Freejoy-User-Guide.docx` from the Markdown pages in `docs/user-guide/`. The pages stay the source of truth; edit them, rebuild, commit both.

- **Rebuild:** `cd docs/user-guide/build-docx && npm ci && npm run build` (Node 22+). A missing image, link target or unlisted page fails the build with the file and line.
- **Check:** `npm test`, then `uv run --no-project --with python-docx==1.2.0 --with markdown-it-py==3.0.0 verify.py` (cross-checks counts, links and pictures against the Markdown with a second parser).
- **New page or section:** add it to `src/config.mjs` (the build refuses to guess).
- **Why `docx` (npm), not python-docx:** it has first-class numbering lists, bookmarks, internal hyperlinks, fields, headers/footers and image alt text, so only the TOC field is hand-written XML. python-docx would need raw XML for nearly all of those. Parsing uses `markdown-it`.
- **Pinned and safe:** `docx` 9.8.1, `markdown-it` 14.3.3, `jszip` 3.10.1 (all MIT; jszip is MIT or GPL, MIT taken), exact versions in `package-lock.json`, `.npmrc` sets `ignore-scripts=true`. This folder is separate from `nextjs-frontend/` and `fastapi_backend/`.
- **Contents page:** a real Word TOC field (levels 1-3, set to update when Word opens the file; accept the prompt) with a stored list of clickable entries for viewers that never update fields. Those stored entries have no page numbers until Word refreshes the field.
- **Output is reproducible:** same sources give the same bytes (zip and document dates come from the "Last updated" line in `README.md`; random Word ids are renumbered 1, 2, 3...).
- **Screenshots:** embedded unchanged. Desktop shots are up to 16 cm wide, phone shots 6 cm; a desktop and phone shot of the same screen go side by side in one table when both fit a page.
- **Tall screenshots are sliced, not shrunk:** a shot taller than 22 cm is first shrunk to fit a page, but only while it keeps 85% (desktop) or 92% (phone) of its width. Otherwise it is cut into equal slices, each a separate picture paragraph that points at the same embedded file and crops it with `a:srcRect` top/bottom (docx `crop` option, no image library). Slices are numbered in their alt text ("... (part 2 of 3)"), keep with the next paragraph, and the figure has one caption after the last slice. A tall phone shot never forces a pair table; the two shots then follow each other.
