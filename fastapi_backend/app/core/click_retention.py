"""How long outbound click rows are kept.

One value for the retention job (``commands.prune_click_events``) and for the merchant click report,
which tells the admin when a date range reaches back past the data. The period is also stated on the
privacy page ("If you use an offer button"): change both together.
"""

CLICK_RETENTION_DAYS = 395  # 13 months
