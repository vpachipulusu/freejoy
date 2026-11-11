"""How much a single merchant may put in front of the editors (ADR-013, FREEJ-58).

One key must not be able to flood the review queue. Both are counted in the database inside the transaction that
inserts the submission, under a per-merchant advisory lock, so concurrent requests cannot both pass the count. A
refusal is a 429 with the code ``open_submission_cap`` or ``daily_submission_cap``: an update of a ref that already
exists is never refused by either, only a new submission.
"""

from typing import Final

# Submissions whose offer is still a DRAFT (waiting for an editor, or sent back to one), per merchant.
MAX_OPEN_SUBMISSIONS: Final = 50
# New submissions per merchant per UTC calendar day.
MAX_NEW_SUBMISSIONS_PER_DAY: Final = 20
