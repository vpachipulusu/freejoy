// @vitest-environment node
/**
 * The edit page hands the review token it read to the publish and verify buttons, so the buttons approve exactly
 * the offer that was loaded with them (the API answers 409 `offer_changed` if it moved since).
 */
import { isValidElement, type ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ fetchAdminOffer: vi.fn() }));

vi.mock('next/navigation', () => ({
  notFound: () => {
    throw new Error('NOT_FOUND');
  },
}));
vi.mock('@/lib/logger', () => ({ logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn() } }));
vi.mock('@/app/(admin)/admin/_lib/require-admin-page', () => ({
  requireAdminPage: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('@/app/(admin)/admin/_lib/pick-lists', () => ({
  TRUNCATED_NOTICE: 'truncated',
  loadCategoryOptions: vi.fn().mockResolvedValue({
    ok: true,
    options: [{ id: 'c1', name: 'Food' }],
    truncated: false,
  }),
  loadMerchantOptions: vi.fn().mockResolvedValue({
    ok: true,
    options: [{ id: 'm1', name: 'Acme' }],
    truncated: false,
  }),
}));
vi.mock('@/lib/api/admin-offers', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/api/admin-offers')>('@/lib/api/admin-offers');
  return { ...actual, fetchAdminOffer: mocks.fetchAdminOffer };
});

import EditOfferPage from '@/app/(admin)/admin/offers/[id]/page';
import { OfferLifecycleButtons } from '@/app/(admin)/admin/offers/_components/offer-lifecycle-buttons';

const OFFER = {
  id: 'o1',
  title: 'Free coffee',
  slug: 'free-coffee',
  category_id: 'c1',
  merchant_id: 'm1',
  offer_type: 'FREE_SAMPLE',
  status: 'DRAFT',
  featured: false,
  end_at: null,
  last_verified_at: null,
  updated_at: '2026-03-06T10:00:00Z',
  review_token: 'token-from-this-read',
  summary: 'A sachet.',
  description: [],
  image_tint: 'mint',
  image_label: 'coffee',
  image_alt: 'A sachet',
  source_url: null,
  postage_kind: 'FREE',
  postage_amount_minor: null,
  postage_currency: null,
  requires_card: false,
  subscription_terms: null,
  eligibility: 'UK',
  availability: 'Now',
  steps: [],
  start_at: null,
  freshness: null,
  publisher_notes: null,
  created_at: '2026-03-01T10:00:00Z',
  links: [],
};

function findAll(node: ReactNode, type: unknown, found: unknown[] = []): unknown[] {
  if (Array.isArray(node)) {
    for (const child of node) findAll(child, type, found);
  } else if (isValidElement<{ children?: ReactNode }>(node)) {
    if (node.type === type) found.push(node);
    findAll(node.props.children, type, found);
  }
  return found;
}

beforeEach(() => {
  mocks.fetchAdminOffer.mockReset();
});

describe('edit offer page', () => {
  it('passes the review token it read to the lifecycle buttons', async () => {
    mocks.fetchAdminOffer.mockResolvedValue({ success: true, data: OFFER });

    const page = await EditOfferPage({ params: Promise.resolve({ id: 'o1' }) });

    const [buttons] = findAll(page as ReactNode, OfferLifecycleButtons) as {
      props: { offerId: string; reviewToken: string };
    }[];
    expect(buttons.props).toMatchObject({ offerId: 'o1', reviewToken: 'token-from-this-read' });
  });
});
