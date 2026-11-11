/**
 * The offer page's partner panel: the "Submitted by retailer" marker, the comparison (two labelled regions per
 * changed field), apply and dismiss (with a confirmation), double presses, the wait while the form is dirty, and
 * where focus goes when the buttons are replaced after a write.
 */
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mocks = vi.hoisted(() => ({
  applyPartnerUpdateAction: vi.fn(),
  dismissPartnerUpdateAction: vi.fn(),
  toast: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock('@/app/actions/admin/offer-actions', () => ({
  applyPartnerUpdateAction: mocks.applyPartnerUpdateAction,
  dismissPartnerUpdateAction: mocks.dismissPartnerUpdateAction,
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: mocks.refresh, push: vi.fn() }),
}));

import { PartnerSubmissionPanel } from '@/app/(admin)/admin/offers/_components/partner-submission-panel';
import { OfferReviewProvider } from '@/app/(admin)/admin/offers/_components/offer-review-context';
import {
  DirtyFormProvider,
  useReportFormDirty,
} from '@/app/(admin)/admin/_components/dirty-form-context';
import type { OfferDetailView } from '@/lib/admin-catalogue/offer-view';

const HASH = 'a'.repeat(64);
const BASE = {
  id: 'o1',
  title: 'Editor title',
  summary: 'Summary',
  description: [],
  offerType: 'FREE_SAMPLE',
  categoryId: 'c1',
  postageKind: 'FREE',
  postageAmountMinor: null,
  postageCurrency: null,
  requiresCard: false,
  subscriptionTerms: null,
  eligibility: 'UK',
  availability: 'Stocks',
  steps: [],
  startAt: null,
  endAt: null,
  reviewToken: 'tok-1',
  links: [
    { id: 'l1', provider: 'direct', destinationUrl: 'https://shop.example.com/a', active: true },
  ],
};
const PAYLOAD = {
  title: 'Retailer title',
  summary: 'Summary',
  description: [],
  offer_type: 'FREE_SAMPLE',
  category_id: 'c1',
  postage_kind: 'FREE',
  postage_amount_minor: null,
  postage_currency: null,
  requires_card: false,
  subscription_terms: null,
  eligibility: 'UK',
  availability: 'Stocks',
  steps: [],
  start_at: null,
  end_at: null,
  destination_url: 'https://shop.example.com/a',
};

function offer(pending: boolean): OfferDetailView {
  return {
    ...BASE,
    partnerSubmission: {
      ref: 'sku-1',
      merchantId: 'm1',
      merchantName: 'Acme Ltd',
      submittedAt: '2026-02-01T09:00:00Z',
      pending: pending
        ? { hash: HASH, submittedAt: '2026-02-05T09:00:00Z', payload: PAYLOAD }
        : null,
    },
  } as unknown as OfferDetailView;
}

function Dirty() {
  useReportFormDirty(true);
  return null;
}

function tree(
  item: OfferDetailView,
  extras: { dirty?: boolean } = {},
  status = 'DRAFT'
): ReactNode {
  return (
    <DirtyFormProvider>
      <OfferReviewProvider>
        {extras.dirty ? <Dirty /> : null}
        <PartnerSubmissionPanel
          offer={item}
          status={status as never}
          categoryNames={{ c1: 'Home' }}
        />
      </OfferReviewProvider>
    </DirtyFormProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.applyPartnerUpdateAction.mockResolvedValue({
    success: true,
    data: { id: 'o1', slug: 's', status: 'DRAFT', reviewToken: 'tok-2' },
  });
  mocks.dismissPartnerUpdateAction.mockResolvedValue({
    success: true,
    data: { id: 'o1', slug: 's', status: 'DRAFT', reviewToken: 'tok-2' },
  });
});

describe('the partner panel', () => {
  it('names the merchant and the ref, and shows no update section when nothing waits', () => {
    render(tree(offer(false)));

    expect(screen.getByRole('heading', { name: 'Submitted by retailer' })).toBeInTheDocument();
    expect(screen.getByText('Acme Ltd')).toBeInTheDocument();
    expect(screen.getByText('sku-1')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Retailer update' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /retailer update/i })).not.toBeInTheDocument();
  });

  it('renders nothing for an offer that did not come through the partner API', () => {
    const { container } = render(
      tree({ ...offer(false), partnerSubmission: null } as unknown as OfferDetailView)
    );

    expect(container).toBeEmptyDOMElement();
  });

  it('shows each changed field as two labelled regions and only names the unchanged ones', () => {
    render(tree(offer(true)));

    const current = screen.getByRole('group', { name: 'Title, current' });
    const proposed = screen.getByRole('group', { name: 'Title, proposed' });
    expect(within(current).getByText('Editor title')).toBeInTheDocument();
    expect(within(proposed).getByText('Retailer title')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Summary, current' })).not.toBeInTheDocument();
    expect(screen.getByText(/Not changed:.*Summary/)).toBeInTheDocument();
  });

  it('applies with the token and the hash it showed, and announces the outcome', async () => {
    const user = userEvent.setup();
    render(tree(offer(true)));

    await user.click(screen.getByRole('button', { name: 'Apply retailer update' }));

    await waitFor(() =>
      expect(mocks.applyPartnerUpdateAction).toHaveBeenCalledWith('o1', 'tok-1', HASH)
    );
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Retailer update applied' })
    );
  });

  it('ignores a second press while the first write is running', async () => {
    const user = userEvent.setup();
    let release!: (value: unknown) => void;
    mocks.applyPartnerUpdateAction.mockReturnValue(new Promise((done) => (release = done)));
    render(tree(offer(true)));
    const apply = screen.getByRole('button', { name: 'Apply retailer update' });

    await user.click(apply);
    await user.click(apply);

    expect(mocks.applyPartnerUpdateAction).toHaveBeenCalledTimes(1);
    expect(apply).toHaveAttribute('aria-disabled', 'true');
    release({ success: true, data: { id: 'o1', slug: 's', status: 'DRAFT' } });
  });

  it('dismisses only after a confirmation, and Cancel changes nothing', async () => {
    const user = userEvent.setup();
    render(tree(offer(true)));

    await user.click(screen.getByRole('button', { name: 'Dismiss retailer update' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(mocks.dismissPartnerUpdateAction).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Dismiss retailer update' }));
    await user.click(screen.getByRole('button', { name: 'Dismiss update' }));

    await waitFor(() =>
      expect(mocks.dismissPartnerUpdateAction).toHaveBeenCalledWith('o1', 'tok-1', HASH)
    );
  });

  it('waits (aria-disabled) while the form beside it has unsaved edits', async () => {
    const user = userEvent.setup();
    render(tree(offer(true), { dirty: true }));
    const apply = screen.getByRole('button', { name: 'Apply retailer update' });

    await user.click(apply);

    expect(apply).toHaveAttribute('aria-disabled', 'true');
    expect(mocks.applyPartnerUpdateAction).not.toHaveBeenCalled();
  });

  it('offers no apply on an archived offer but still lets the update be dismissed', () => {
    render(tree(offer(true), {}, 'ARCHIVED'));

    expect(screen.queryByRole('button', { name: 'Apply retailer update' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dismiss retailer update' })).toBeInTheDocument();
    expect(screen.getByText(/can no longer be changed here/)).toBeInTheDocument();
  });

  it('moves focus to a stable paragraph when the buttons go away after a write', async () => {
    const user = userEvent.setup();
    const { rerender } = render(tree(offer(true)));
    await user.click(screen.getByRole('button', { name: 'Apply retailer update' }));
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());

    rerender(tree({ ...offer(false), reviewToken: 'tok-2' } as unknown as OfferDetailView));

    const done = await screen.findByText('The retailer update has been dealt with.');
    await waitFor(() => expect(done).toHaveFocus());
  });
});
