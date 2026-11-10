/**
 * Breadcrumb filter for the browser (FREEJ-28). Sentry's click breadcrumbs name the element that was clicked,
 * for example `button[aria-label="Save Glow-boosting face cream"]`. The Save heart's name is the offer's
 * title, so a click on it would tell the monitoring provider which freebie someone saved. Those breadcrumbs
 * lose their message; every other breadcrumb passes through untouched.
 */
const SAVE_BUTTON_MARK = 'aria-label="Save ';

interface BreadcrumbLike {
  category?: string;
  message?: string;
}

export function scrubBreadcrumb<T extends BreadcrumbLike>(breadcrumb: T): T {
  if (
    breadcrumb.category === 'ui.click' &&
    typeof breadcrumb.message === 'string' &&
    breadcrumb.message.includes(SAVE_BUTTON_MARK)
  ) {
    return { ...breadcrumb, message: '' };
  }
  return breadcrumb;
}
