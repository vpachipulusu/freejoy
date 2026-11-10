import * as Sentry from '@sentry/nextjs';
import { scrubBreadcrumb } from '@/lib/sentry/scrub-breadcrumb';
import { scrubEvent } from '@/lib/sentry/scrub-event';

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  enabled: !!process.env.NEXT_PUBLIC_SENTRY_DSN,

  // One-use alert tokens travel in page addresses (/alerts/confirm?token=...) and an email address must
  // never reach monitoring through a URL (FREEJ-31): scrubEvent filters both, in every URL and query shape.
  beforeSend: (event) => scrubEvent(event),
  beforeSendTransaction: (event) => scrubEvent(event),
  // A click on a Save heart is named after the offer's title (FREEJ-28): keep it out of breadcrumbs.
  beforeBreadcrumb: (breadcrumb) => scrubBreadcrumb(breadcrumb),
  environment: process.env.NODE_ENV,

  // Performance: low sample rate in production
  tracesSampleRate: process.env.NODE_ENV === 'production' ? 0.1 : 1.0,

  // Session Replay disabled (heavy on performance)
  replaysSessionSampleRate: 0,
  replaysOnErrorSampleRate: 0,
});

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
