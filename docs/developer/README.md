# Developer docs

Start here, then follow the links. Root-level overview: [`../../README.md`](../../README.md). Agent rules:
[`../../.claude/CLAUDE.md`](../../.claude/CLAUDE.md).

## Setup and workflow

| Doc | What it covers |
| --- | --- |
| [getting-started.md](getting-started.md) | Prerequisites, env variables, running both apps, Docker Compose |
| [workflow.md](workflow.md) | Branches, commits, PRs, CI gate, pre-commit, the OpenAPI client, migrations |
| [modules.md](modules.md) | The optional Stripe Billing and Capacitor modules: files, env, touch points, removal |
| [template-sync.md](template-sync.md) | Keeping a project in step with this template (and feeding fixes back) |
| [testing.md](testing.md) | Unit, integration and e2e tests in both apps; coverage rules |
| [local-e2e.md](local-e2e.md) | Running Playwright locally against a production build and a real backend |
| [security.md](security.md) | Secrets, server-only code, session and CSRF rules, e2e accounts |
| [vercel-previews.md](vercel-previews.md) | Deploying with Vercel services (frontend + backend in one project) |

## Architecture

| Doc | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | The two apps, the auth flow, the backend layering, data, logging |
| [freejoy-frontend.md](freejoy-frontend.md) | Freejoy public pages: routes, catalogue source (API or fixtures), the API wrapper and its cache, states, trust rules, theme, tests |
| [../compliance/uk-affiliate-checklist.md](../compliance/uk-affiliate-checklist.md) | UK affiliate and consumer-trust checklist (PECR/ICO, CAP/ASA/CMA): status per rule, Phase 3 acceptance criteria, Phase 1 copy audit, owner actions |
| [freejoy-seo.md](freejoy-seo.md) | Freejoy SEO: indexing policy, sitemap, robots, JSON-LD, expired offers, owner follow-ups |
| [platform-models.md](platform-models.md) | SQLAlchemy models and the Alembic baseline |
| [backend-auth.md](backend-auth.md) | Session endpoints, Firebase verification, internal key |
| [backend-totp.md](backend-totp.md) | Two-factor authentication |
| [backend-profile.md](backend-profile.md) | `/v1/me`, password change, data export, account deletion |
| [backend-organizations.md](backend-organizations.md) | Organisations and memberships |
| [backend-plan-limits.md](backend-plan-limits.md) | Plan definitions and limit checks |
| [backend-public-settings.md](backend-public-settings.md) | Public settings read by the frontend |
| [backend-catalogue.md](backend-catalogue.md) | Public catalogue API: offers, offer detail, categories |
| [freejoy-affiliate.md](freejoy-affiliate.md) | The `/go/[offerId]` affiliate redirect: flow, validation, what a click stores, provider host allowlist |
| [backend-offer-health.md](backend-offer-health.md) | The `check_offer_health` command: expires ended offers, reports dead offer and `direct` link destinations, never requests tracking links, SSRF controls, flags, exit codes, scheduling |
| [backend-awin-import.md](backend-awin-import.md) | The `import_awin_offers` command: Awin promotions and vouchers (joined programmes, UK) become DRAFT offers; never publishes, edit protection, provenance tables, settings, flags, exit codes, the 20-per-minute throttle, and the assumptions about the response that are not yet verified against the live API |
| [backend-network-reports.md](backend-network-reports.md) | The `import_awin_reports` command: Awin's own transaction report (status, commission and sale amounts, click reference) stored in `network_report_rows`; revenue comes from reports, never from clicks; matching rules and limits, the unverified-assumptions list |
| [backend-subscribers.md](backend-subscribers.md) | Alerts sign-up: double opt-in, confirm, one-click unsubscribe, email provider, retention command, privacy rules |
| [backend-frontend-seams.md](backend-frontend-seams.md) | Endpoints that keep `stripe` and `firebase-admin` out of the frontend |
| [backend-audit-logs.md](backend-audit-logs.md) | Audit log model and admin API |
| [backend-admin-users.md](backend-admin-users.md) | Admin user management |
| [backend-admin-catalogue.md](backend-admin-catalogue.md) | Admin offers, categories, merchants and affiliate links: permissions, publish rules, status table |
| [admin-offers.md](admin-offers.md) | The admin freebies UI: list, offer editor, publish workflow, categories and merchants, server actions, tests, Journey 4 |
| [admin-settings.md](admin-settings.md) | The admin settings page (read-only system stats) and how the currency setting is stored |

## Stripe Billing module

| Doc | What it covers |
| --- | --- |
| [backend-org-billing.md](backend-org-billing.md) | Checkout, Billing Portal and subscription state per organisation |
| [backend-billing-webhook.md](backend-billing-webhook.md) | `POST /webhooks/stripe`, idempotency, production checklist |
| [backend-admin-subscriptions.md](backend-admin-subscriptions.md) | Admin subscription list and grants |
| [backend-admin-analytics.md](backend-admin-analytics.md) | Admin analytics and Stripe sync |

End-user guide (what each screen does, with screenshots): [`../user-guide/README.md`](../user-guide/README.md). Word version
(one file, rebuilt from those pages with `docs/user-guide/build-docx`): [`../user-guide/Freejoy-User-Guide.docx`](../user-guide/Freejoy-User-Guide.docx).

Decisions: [`../adr/README.md`](../adr/README.md).
