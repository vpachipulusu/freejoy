/**
 * The offer page's partner panel: the "Submitted by retailer" marker, the comparison (two named groups per changed
 * field), apply and dismiss with their confirmations, double presses, the waits (dirty form, reload, another write)
 * with their reasons, a failure that stays on the page, a stale proposal, and where focus goes.
 */
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mocks = vi.hoisted(() => ({
  applyPartnerUpdateAction: vi.fn(),
  dismissPartnerUpdateAction: vi.fn(),
  offerLifecycleAction: vi.fn(),
  toast: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock('@/app/actions/admin/offer-actions', () => ({
  applyPartnerUpdateAction: mocks.applyPartnerUpdateAction,
  dismissPartnerUpdateAction: mocks.dismissPartnerUpdateAction,
  offerLifecycleAction: mocks.offerLifecycleAction,
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: mocks.refresh, push: vi.fn() }),
}));

import { PartnerSubmissionPanel } from '@/app/(admin)/admin/offers/_components/partner-submission-panel';
import { OfferLifecycleButtons } from '@/app/(admin)/admin/offers/_components/offer-lifecycle-buttons';
import {
  OfferReviewProvider,
  useOfferReview,
} from '@/app/(admin)/admin/offers/_components/offer-review-context';
import {
  DirtyFormProvider,
  useReportFormBusy,
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

function offer(
  pending: boolean,
  payload: Record<string, unknown> = {},
  overrides: Record<string, unknown> = {}
): OfferDetailView {
  return {
    ...BASE,
    ...overrides,
    partnerSubmission: {
      ref: 'sku-1',
      merchantId: 'm1',
      merchantName: 'Acme Ltd',
      submittedAt: '2026-02-01T09:00:00Z',
      pending: pending
        ? {
            hash: HASH,
            submittedAt: '2026-02-05T09:00:00Z',
            payload: { ...PAYLOAD, ...payload },
          }
        : null,
    },
  } as unknown as OfferDetailView;
}

function Dirty() {
  useReportFormDirty(true);
  return null;
}

function Reloading() {
  useReportFormBusy(true);
  return null;
}

/** Stands for another control on the page (the form, a lifecycle step) whose write is in flight. */
function OtherWriter() {
  const review = useOfferReview();
  return (
    <>
      <button type="button" onClick={() => review.beginWrite()}>
        Begin other write
      </button>
      <button type="button" onClick={() => review.recordWrite(undefined)}>
        End other write
      </button>
    </>
  );
}

type Extras = { dirty?: boolean; reloading?: boolean; other?: boolean; lifecycle?: boolean };

function tree(item: OfferDetailView, extras: Extras = {}, status = 'DRAFT'): ReactNode {
  return (
    <DirtyFormProvider>
      <OfferReviewProvider>
        {extras.dirty ? <Dirty /> : null}
        {extras.reloading ? <Reloading /> : null}
        {extras.other ? <OtherWriter /> : null}
        {extras.lifecycle ? (
          <OfferLifecycleButtons
            offerId="o1"
            offerTitle="Editor title"
            status={status as never}
            reviewToken={item.reviewToken}
            commercialBasis="EDITORIAL"
            confirmBasisToPublish={false}
          />
        ) : null}
        <PartnerSubmissionPanel
          offer={item}
          status={status as never}
          categoryNames={{ c1: 'Home' }}
        />
      </OfferReviewProvider>
    </DirtyFormProvider>
  );
}

const SAVED = {
  success: true,
  data: { id: 'o1', slug: 's', status: 'DRAFT', reviewToken: 'tok-2' },
};
const REFUSED = { success: false, error: 'Applying is not possible right now.' };
const region = () => screen.getByRole('region', { name: 'Retailer update' });
const applyButton = (name = 'Apply retailer update') => screen.getByRole('button', { name });
const dismissButton = () => screen.getByRole('button', { name: 'Dismiss retailer update' });

beforeEach(() => {
  vi.clearAllMocks();
  mocks.applyPartnerUpdateAction.mockResolvedValue(SAVED);
  mocks.dismissPartnerUpdateAction.mockResolvedValue(SAVED);
  mocks.offerLifecycleAction.mockResolvedValue(SAVED);
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
});

describe('the comparison', () => {
  it('shows each changed field as two groups named by the field and their own label, and only names the unchanged ones', () => {
    render(tree(offer(true)));

    const current = screen.getByRole('group', { name: 'Title Current' });
    const proposed = screen.getByRole('group', { name: 'Title Proposed' });
    expect(within(current).getByText('Editor title')).toBeInTheDocument();
    expect(within(proposed).getByText('Retailer title')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: /^Summary/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Not changed:.*Summary/)).toBeInTheDocument();
  });

  it('uses a paragraph for one line and a list only for several, so nothing is announced as "list, 1 item"', () => {
    render(tree(offer(true, { description: ['One', 'Two'] })));

    const title = screen.getByRole('group', { name: 'Title Proposed' });
    expect(within(title).queryByRole('list')).not.toBeInTheDocument();
    const description = screen.getByRole('group', { name: 'Description Proposed' });
    expect(within(description).getAllByRole('listitem')).toHaveLength(2);
  });

  it('lets a long value wrap instead of overflowing, and stacks the two sides below 640px', () => {
    const long = `https://shop.example.com/a?${'utm_campaign=spring_2026&'.repeat(8)}`;
    render(tree(offer(true, { destination_url: long })));

    const proposed = screen.getByRole('group', { name: 'Retailer link Proposed' });
    expect(within(proposed).getByText(long)).toHaveClass('[overflow-wrap:anywhere]');
    expect(proposed).toHaveClass('min-w-0');
    expect(proposed.parentElement).toHaveClass('grid-cols-1', 'sm:grid-cols-2');
  });

  it('never makes the retailer link a link', () => {
    render(tree(offer(true, { destination_url: 'https://shop.example.com/b' })));

    expect(screen.getByText('https://shop.example.com/b')).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('says nothing differs when the proposal equals the offer, and still offers both choices', () => {
    render(tree(offer(true, { title: 'Editor title' })));

    expect(screen.getByText(/Nothing in the update differs from the offer/)).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: /Current$/ })).not.toBeInTheDocument();
    expect(dismissButton()).toBeInTheDocument();
  });

  it('shows the time and UTC of a date, and sees a change of time on the same day', () => {
    render(
      tree(offer(true, { end_at: '2026-12-31T23:59:00+00:00' }, { endAt: '2026-12-31T09:00:00Z' }))
    );

    const proposed = screen.getByRole('group', { name: 'Ends (UTC) Proposed' });
    expect(within(proposed).getByText('31 Dec 2026, 23:59 UTC')).toBeInTheDocument();
    expect(
      within(screen.getByRole('group', { name: 'Ends (UTC) Current' })).getByText(
        '31 Dec 2026, 09:00 UTC'
      )
    ).toBeInTheDocument();
  });

  it('says the retailer link will not be applied when the offer has two direct links', () => {
    const two = offer(
      true,
      { destination_url: 'https://shop.example.com/new' },
      {
        links: [
          ...BASE.links,
          {
            id: 'l2',
            provider: 'direct',
            destinationUrl: 'https://shop.example.com/z',
            active: true,
          },
        ],
      }
    );
    render(tree(two));

    expect(
      within(region()).getByText(
        "The retailer's link will not be applied because this offer has 2 direct links. Update the link yourself."
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Link sent \(shown as text\): https:\/\/shop.example.com\/new/)
    ).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: /^Retailer link/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Not changed:/).textContent).not.toMatch(/Retailer link/);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('adds no note when the retailer link is the offer link', () => {
    render(tree(offer(true)));

    expect(screen.queryByText(/will not be applied/)).not.toBeInTheDocument();
  });

  it('survives a payload of the wrong shapes and prototype names, and never reads them as markup', () => {
    render(
      tree(
        offer(true, {
          title: '<img src=x onerror=alert(1)>',
          postage_kind: 'constructor',
          offer_type: '__proto__',
          category_id: 'toString',
          steps: [null, 5, 'x'],
          destination_url: 7,
          requires_card: 'yes',
        })
      )
    );

    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(screen.getByText('Unknown category')).toBeInTheDocument();
    expect(screen.getAllByText('Unreadable value').length).toBeGreaterThanOrEqual(4);
    expect(document.querySelector('img')).toBeNull();
  });

  it('says a category outside the pick list is not in it, and an unnamed one when the list is complete', () => {
    const { rerender } = render(tree(offer(true, { category_id: 'c9' })));
    expect(screen.getByText('Unknown category')).toBeInTheDocument();

    rerender(
      <DirtyFormProvider>
        <OfferReviewProvider>
          <PartnerSubmissionPanel
            offer={offer(true, { category_id: 'c9' })}
            status="DRAFT"
            categoryNames={{ c1: 'Home' }}
            categoriesTruncated
          />
        </OfferReviewProvider>
      </DirtyFormProvider>
    );
    expect(screen.getByText('Category not in this list')).toBeInTheDocument();
  });
});

describe('applying', () => {
  it('applies a draft in one press, with the token and the hash it showed, and announces the outcome', async () => {
    const user = userEvent.setup();
    render(tree(offer(true)));

    await user.click(applyButton());

    await waitFor(() =>
      expect(mocks.applyPartnerUpdateAction).toHaveBeenCalledWith('o1', 'tok-1', HASH)
    );
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Retailer update applied' })
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('says the offer and its page have not changed, without the live wording, for a draft', () => {
    render(tree(offer(true)));

    expect(within(region()).getByText(/The offer has not changed\./)).toBeInTheDocument();
    expect(screen.queryByText('Applying changes the published page straight away.')).toBeNull();
  });

  it('ignores a second press while the first write is running, and finishes the write', async () => {
    const user = userEvent.setup();
    let release!: (value: unknown) => void;
    mocks.applyPartnerUpdateAction.mockReturnValue(new Promise((done) => (release = done)));
    render(tree(offer(true)));
    const apply = applyButton();

    await user.click(apply);
    await user.click(apply);

    expect(mocks.applyPartnerUpdateAction).toHaveBeenCalledTimes(1);
    expect(apply).toHaveAttribute('aria-disabled', 'true');
    await act(async () => {
      release({ success: true, data: { id: 'o1', slug: 's', status: 'DRAFT' } });
    });
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalledTimes(1));
  });

  it('has an empty live region before anything happens, and names the offer while it works', async () => {
    const user = userEvent.setup();
    let release!: (value: unknown) => void;
    mocks.applyPartnerUpdateAction.mockReturnValue(new Promise((done) => (release = done)));
    render(tree(offer(true)));
    const status = within(region()).getByRole('status');
    expect(status).toBeEmptyDOMElement();

    await user.click(applyButton());

    await waitFor(() => expect(status).toHaveTextContent('Updating Editor title'));
    expect(within(region()).getByRole('status')).toBe(status);
    await act(async () => {
      release(SAVED);
    });
    await waitFor(() => expect(status).toBeEmptyDOMElement());
  });

  it('offers no apply on an archived offer but still lets the update be dismissed', () => {
    render(tree(offer(true), {}, 'ARCHIVED'));

    expect(screen.queryByRole('button', { name: /^Apply/ })).not.toBeInTheDocument();
    expect(dismissButton()).toBeInTheDocument();
    expect(screen.getByText(/can no longer be changed here/)).toBeInTheDocument();
  });

  it('moves focus to a stable paragraph when the buttons go away after a write', async () => {
    const user = userEvent.setup();
    const { rerender } = render(tree(offer(true)));
    await user.click(applyButton());
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());

    rerender(tree({ ...offer(false), reviewToken: 'tok-2' } as unknown as OfferDetailView));

    const done = await screen.findByText('The retailer update has been dealt with.');
    await waitFor(() => expect(done).toHaveFocus());
  });
});

describe('applying to a live offer', () => {
  const live = () => render(tree(offer(true), {}, 'PUBLISHED'));
  const APPLY = 'Apply to the published offer';
  const TITLE = 'Apply this retailer update to the published offer?';

  it('says it changes the published page straight away and labels the button for the published offer', () => {
    live();

    expect(
      within(region()).getByText(/The offer and its published page have not changed\./)
    ).toBeInTheDocument();
    expect(
      screen.getByText('Applying changes the published page straight away.')
    ).toBeInTheDocument();
    expect(applyButton(APPLY)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Apply retailer update' })).toBeNull();
  });

  it('opens a confirmation naming the changed fields and does not write until it is confirmed', async () => {
    const user = userEvent.setup();
    live();

    await user.click(applyButton(APPLY));

    const dialog = await screen.findByRole('dialog', { name: TITLE });
    expect(within(dialog).getByText(/Changed: Title/)).toBeInTheDocument();
    expect(mocks.applyPartnerUpdateAction).not.toHaveBeenCalled();
  });

  it.each([
    [
      'Cancel',
      async (user: ReturnType<typeof userEvent.setup>) =>
        user.click(screen.getByRole('button', { name: 'Cancel' })),
    ],
    ['Escape', async (user: ReturnType<typeof userEvent.setup>) => user.keyboard('{Escape}')],
  ])('%s closes it without writing and returns focus to Apply', async (_name, close) => {
    const user = userEvent.setup();
    live();
    await user.click(applyButton(APPLY));
    await screen.findByRole('dialog', { name: TITLE });

    await close(user);

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(applyButton(APPLY)).toHaveFocus());
    expect(mocks.applyPartnerUpdateAction).not.toHaveBeenCalled();
  });

  it('applies once when confirmed, even when the confirm button is pressed twice', async () => {
    const user = userEvent.setup();
    live();
    await user.click(applyButton(APPLY));
    const dialog = await screen.findByRole('dialog', { name: TITLE });

    await user.dblClick(within(dialog).getByRole('button', { name: 'Apply update' }));

    await waitFor(() =>
      expect(mocks.applyPartnerUpdateAction).toHaveBeenCalledWith('o1', 'tok-1', HASH)
    );
    expect(mocks.applyPartnerUpdateAction).toHaveBeenCalledTimes(1);
  });

  it('closes the confirmation when the proposal it was opened for is replaced', async () => {
    const user = userEvent.setup();
    const { rerender } = live();
    await user.click(applyButton(APPLY));
    await screen.findByRole('dialog', { name: TITLE });

    const next = offer(true) as unknown as { partnerSubmission: { pending: { hash: string } } };
    next.partnerSubmission.pending.hash = 'b'.repeat(64);
    rerender(tree(next as unknown as OfferDetailView, {}, 'PUBLISHED'));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mocks.applyPartnerUpdateAction).not.toHaveBeenCalled();
  });
});

describe('dismissing', () => {
  const TITLE = 'Dismiss this retailer update?';

  it('asks in a dialog with a title, and Cancel changes nothing and returns focus', async () => {
    const user = userEvent.setup();
    render(tree(offer(true)));

    await user.click(dismissButton());
    expect(await screen.findByRole('dialog', { name: TITLE })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(dismissButton()).toHaveFocus());
    expect(mocks.dismissPartnerUpdateAction).not.toHaveBeenCalled();
  });

  it('closes on Escape and returns focus to the button that opened it', async () => {
    const user = userEvent.setup();
    render(tree(offer(true)));
    await user.click(dismissButton());
    await screen.findByRole('dialog', { name: TITLE });

    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(dismissButton()).toHaveFocus());
    expect(mocks.dismissPartnerUpdateAction).not.toHaveBeenCalled();
  });

  it('dismisses with the token and the hash after the confirmation, once, however often confirm is pressed', async () => {
    const user = userEvent.setup();
    render(tree(offer(true)));
    await user.click(dismissButton());
    const dialog = await screen.findByRole('dialog', { name: TITLE });

    await user.dblClick(within(dialog).getByRole('button', { name: 'Dismiss update' }));

    await waitFor(() =>
      expect(mocks.dismissPartnerUpdateAction).toHaveBeenCalledWith('o1', 'tok-1', HASH)
    );
    expect(mocks.dismissPartnerUpdateAction).toHaveBeenCalledTimes(1);
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Retailer update dismissed' })
    );
  });

  it('moves focus to the stable paragraph when the buttons go away after a dismissal', async () => {
    const user = userEvent.setup();
    const { rerender } = render(tree(offer(true)));
    await user.click(dismissButton());
    await user.click(await screen.findByRole('button', { name: 'Dismiss update' }));
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());

    rerender(tree({ ...offer(false), reviewToken: 'tok-2' } as unknown as OfferDetailView));

    const done = await screen.findByText('The retailer update has been dealt with.');
    await waitFor(() => expect(done).toHaveFocus());
  });
});

describe('waiting, with the reason attached', () => {
  it('holds both buttons while the form has unsaved edits, says why and describes them by it', async () => {
    const user = userEvent.setup();
    render(tree(offer(true), { dirty: true }));

    for (const button of [applyButton(), dismissButton()]) {
      expect(button).toHaveAttribute('aria-disabled', 'true');
      expect(button).toHaveAccessibleDescription(/save or discard your changes first/i);
    }
    expect(within(region()).getByRole('status')).toHaveTextContent(
      'Save or discard your changes first: applying uses the saved version of the offer.'
    );
    await user.click(applyButton());
    await user.click(dismissButton());
    expect(mocks.applyPartnerUpdateAction).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('says the saved version is loading while the form reloads', () => {
    render(tree(offer(true), { reloading: true }));

    expect(applyButton()).toHaveAttribute('aria-disabled', 'true');
    expect(applyButton()).toHaveAccessibleDescription('Loading the saved version of the offer.');
    expect(within(region()).getByRole('status')).toHaveTextContent(
      'Loading the saved version of the offer.'
    );
  });

  it('describes nothing while free to go', () => {
    render(tree(offer(true)));

    expect(applyButton()).not.toHaveAttribute('aria-describedby');
    expect(applyButton()).not.toHaveAttribute('aria-disabled');
  });

  it('holds while another write on the page runs, and does not call its action', async () => {
    const user = userEvent.setup();
    render(tree(offer(true), { other: true }));

    await user.click(screen.getByRole('button', { name: 'Begin other write' }));
    await user.click(applyButton());

    expect(applyButton()).toHaveAttribute('aria-disabled', 'true');
    expect(within(region()).getByRole('status')).toHaveTextContent(
      'Another change is being saved. Wait for it to finish.'
    );
    expect(mocks.applyPartnerUpdateAction).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'End other write' }));
    await waitFor(() => expect(applyButton()).not.toHaveAttribute('aria-disabled'));
    await user.click(applyButton());
    await waitFor(() => expect(mocks.applyPartnerUpdateAction).toHaveBeenCalledTimes(1));
  });

  it('holds the lifecycle steps while a retailer update is being applied, and the reverse', async () => {
    const user = userEvent.setup();
    let release!: (value: unknown) => void;
    mocks.applyPartnerUpdateAction.mockReturnValue(new Promise((done) => (release = done)));
    render(tree(offer(true), { lifecycle: true }));

    await user.click(applyButton());
    await user.click(screen.getByRole('button', { name: 'Mark verified' }));

    expect(screen.getByRole('button', { name: 'Mark verified' })).toHaveAttribute(
      'aria-disabled',
      'true'
    );
    expect(mocks.offerLifecycleAction).not.toHaveBeenCalled();
    await act(async () => {
      release(SAVED);
    });
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Mark verified' })).not.toHaveAttribute(
        'aria-disabled'
      )
    );

    // And the reverse: a running lifecycle step holds Apply.
    let finish!: (value: unknown) => void;
    mocks.offerLifecycleAction.mockReturnValue(new Promise((done) => (finish = done)));
    await user.click(screen.getByRole('button', { name: 'Mark verified' }));
    await user.click(applyButton());
    expect(applyButton()).toHaveAttribute('aria-disabled', 'true');
    expect(mocks.applyPartnerUpdateAction).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish(SAVED);
    });
  });
});

describe('a refusal', () => {
  it('shows a destructive toast and a lasting alert, and leaves the buttons usable for another try', async () => {
    const user = userEvent.setup();
    mocks.applyPartnerUpdateAction.mockResolvedValue(REFUSED);
    render(tree(offer(true)));

    await user.click(applyButton());

    expect(await within(region()).findByRole('alert')).toHaveTextContent(REFUSED.error);
    expect(mocks.toast).toHaveBeenCalledWith({
      variant: 'destructive',
      title: 'Could not apply the retailer update',
      description: REFUSED.error,
    });
    expect(applyButton()).not.toHaveAttribute('aria-disabled');
    expect(dismissButton()).not.toHaveAttribute('aria-disabled');

    await user.click(applyButton());
    await waitFor(() => expect(mocks.applyPartnerUpdateAction).toHaveBeenCalledTimes(2));
  });

  it('clears the alert when the next attempt starts', async () => {
    const user = userEvent.setup();
    let release!: (value: unknown) => void;
    mocks.applyPartnerUpdateAction.mockResolvedValueOnce(REFUSED);
    render(tree(offer(true)));
    await user.click(applyButton());
    await within(region()).findByRole('alert');

    mocks.applyPartnerUpdateAction.mockReturnValueOnce(new Promise((done) => (release = done)));
    await user.click(applyButton());

    await waitFor(() => expect(within(region()).queryByRole('alert')).not.toBeInTheDocument());
    await act(async () => {
      release(SAVED);
    });
  });

  it('shows the dismissal failure the same way', async () => {
    const user = userEvent.setup();
    mocks.dismissPartnerUpdateAction.mockResolvedValue(REFUSED);
    render(tree(offer(true)));

    await user.click(dismissButton());
    await user.click(await screen.findByRole('button', { name: 'Dismiss update' }));

    expect(await within(region()).findByRole('alert')).toHaveTextContent(REFUSED.error);
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({
        variant: 'destructive',
        title: 'Could not dismiss the retailer update',
      })
    );
  });

  it('turns a server action that throws into the curated failure', async () => {
    const user = userEvent.setup();
    mocks.applyPartnerUpdateAction.mockRejectedValue(new Error('SECRET stack'));
    render(tree(offer(true)));

    await user.click(applyButton());

    const alert = await within(region()).findByRole('alert');
    expect(alert).toHaveTextContent('Something went wrong. Please try again.');
    expect(alert).not.toHaveTextContent('SECRET');
  });

  it('raises the lasting notice and refreshes when the proposal went stale', async () => {
    const user = userEvent.setup();
    mocks.applyPartnerUpdateAction.mockResolvedValue({
      success: false,
      error: 'This offer changed since you opened it. Review it again.',
      reason: 'offer_changed',
    });
    render(tree(offer(true)));

    await user.click(applyButton());

    expect(
      await within(region()).findByText(
        "The offer or the retailer's update changed while you were looking. This is the latest: compare it again."
      )
    ).toBeInTheDocument();
    expect(mocks.refresh).toHaveBeenCalled();
  });

  it('leaves focus where it was when a later reload arrives after a refused write', async () => {
    const user = userEvent.setup();
    mocks.applyPartnerUpdateAction.mockResolvedValue(REFUSED);
    const { rerender } = render(tree(offer(true)));
    await user.click(applyButton());
    await within(region()).findByRole('alert');
    (document.activeElement as HTMLElement).blur();
    expect(document.body).toHaveFocus();

    rerender(tree({ ...offer(true), reviewToken: 'tok-2' } as unknown as OfferDetailView));

    expect(document.body).toHaveFocus();
    expect(screen.queryByText('The retailer update has been dealt with.')).toBeNull();
  });
});
