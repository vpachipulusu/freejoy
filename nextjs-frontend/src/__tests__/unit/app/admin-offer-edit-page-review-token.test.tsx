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
import { OfferProvenancePanel } from '@/app/(admin)/admin/offers/_components/offer-provenance-panel';

const OFFER = {
  id: 'o1',
  title: 'Free coffee',
  slug: 'free-coffee',
  category_id: 'c1',
  merchant_id: 'm1',
  offer_type: 'FREE_SAMPLE',
  commercial_basis: 'SPONSORED',
  status: 'DRAFT',
  featured: false,
  end_at: null,
  last_verified_at: null,
  updated_at: '2026-03-06T10:00:00Z',
  import_provider: null,
  source_changed_at: null,
  provenance: null,
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

  it('hands the buttons the saved basis, and asks for its confirmation only when every active link is direct', async () => {
    const direct = {
      id: 'l1',
      offer_id: 'o1',
      provider: 'direct',
      destination_url: 'https://acme.example/x',
      tracking_id: null,
      active: true,
      created_at: '2026-03-01T10:00:00Z',
      updated_at: '2026-03-01T10:00:00Z',
    };
    const props = async (links: unknown[]) => {
      mocks.fetchAdminOffer.mockResolvedValue({ success: true, data: { ...OFFER, links } });
      const page = await EditOfferPage({ params: Promise.resolve({ id: 'o1' }) });
      const [buttons] = findAll(page as ReactNode, OfferLifecycleButtons) as {
        props: Record<string, unknown>;
      }[];
      return buttons.props;
    };

    expect(await props([direct])).toMatchObject({
      commercialBasis: 'SPONSORED',
      confirmBasisToPublish: true,
    });
    expect(await props([direct, { ...direct, id: 'l2', provider: 'awin' }])).toMatchObject({
      confirmBasisToPublish: false,
    });
    expect(await props([])).toMatchObject({ confirmBasisToPublish: false });
  });

  it('shows the provenance panel of an imported offer, with the token it read and the status', async () => {
    mocks.fetchAdminOffer.mockResolvedValue({
      success: true,
      data: {
        ...OFFER,
        import_provider: 'awin',
        source_changed_at: '2026-03-05T10:00:00Z',
        provenance: {
          provider: 'awin',
          external_id: '1001',
          applied_at: '2026-03-01T10:00:00Z',
          source_changed_at: '2026-03-05T10:00:00Z',
        },
      },
    });

    const page = await EditOfferPage({ params: Promise.resolve({ id: 'o1' }) });

    const [panel] = findAll(page as ReactNode, OfferProvenancePanel) as {
      props: Record<string, unknown>;
    }[];
    expect(panel.props).toMatchObject({
      offerId: 'o1',
      offerTitle: 'Free coffee',
      status: 'DRAFT',
      reviewToken: 'token-from-this-read',
      provenance: {
        provider: 'awin',
        externalId: '1001',
        appliedAt: '2026-03-01T10:00:00Z',
        sourceChangedAt: '2026-03-05T10:00:00Z',
      },
    });
  });

  it('shows no provenance panel for an offer made by hand', async () => {
    mocks.fetchAdminOffer.mockResolvedValue({ success: true, data: OFFER });

    const page = await EditOfferPage({ params: Promise.resolve({ id: 'o1' }) });

    expect(findAll(page as ReactNode, OfferProvenancePanel)).toHaveLength(0);
  });
});
