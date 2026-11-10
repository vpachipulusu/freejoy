import * as Sentry from '@sentry/nextjs';
import { scrubEvent } from '@/lib/sentry/scrub-event';

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  enabled: !!process.env.NEXT_PUBLIC_SENTRY_DSN,

  // One-use alert tokens travel in page addresses (/alerts/confirm?token=...) and an email address must
  // never reach monitoring through a URL (FREEJ-31): scrubEvent filters both, in every URL and query shape.
  beforeSend: (event) => scrubEvent(event),
  beforeSendTransaction: (event) => scrubEvent(event),
  environment: process.env.NODE_ENV,

  // Performance: low sample rate in production
  tracesSampleRate: process.env.NODE_ENV === 'production' ? 0.1 : 1.0,

  // The Saved page posts the visitor's saved offer slugs (FREEJ-28): never capture incoming request bodies.
  // Spans of incoming requests stay on; only their bodies go (scrubEvent drops any that remain).
  integrations: [Sentry.httpIntegration({ maxIncomingRequestBodySize: 'none' })],
});
