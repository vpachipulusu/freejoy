/**
 * The commercial basis in the admin UI (FREEJ-53): a required choice with one line of help per option, shown on
 * the preview card, saved with the form, and confirmed before publishing an offer whose only links are direct.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const SLOW = { timeout: 30_000 };

const mocks = vi.hoisted(() => ({
  createOfferAction: vi.fn(),
  updateOfferAction: vi.fn(),
  offerLifecycleAction: vi.fn(),
  push: vi.fn(),
  refresh: vi.fn(),
  toast: vi.fn(),
}));

vi.mock('@/app/actions/admin/offer-actions', () => ({
  createOfferAction: mocks.createOfferAction,
  updateOfferAction: mocks.updateOfferAction,
  offerLifecycleAction: mocks.offerLifecycleAction,
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mocks.push, refresh: mocks.refresh }),
}));

import { DirtyFormProvider } from '@/app/(admin)/admin/_components/dirty-form-context';
import { OfferForm } from '@/app/(admin)/admin/offers/_components/offer-form';
import { OfferLifecycleButtons } from '@/app/(admin)/admin/offers/_components/offer-lifecycle-buttons';
import { OfferReviewProvider } from '@/app/(admin)/admin/offers/_components/offer-review-context';
import { COMMERCIAL_BASIS_HELP } from '@/lib/admin-catalogue/offer-labels';
import { emptyOfferFormValues, type OfferFormValues } from '@/lib/admin-catalogue/offer-schema';

const categories = [{ id: 'c1', name: 'Food & drink' }];
const merchants = [{ id: 'm1', name: 'Good Beans' }];

const filled: OfferFormValues = {
  ...emptyOfferFormValues(),
  title: 'Free coffee sample',
  summary: 'A free tasting sachet.',
  categoryId: 'c1',
  merchantId: 'm1',
  imageLabel: 'coffee',
  imageAlt: 'A coffee sachet',
  eligibility: 'UK adults.',
  availability: 'While stocks last.',
};

function createForm(initialValues: OfferFormValues = emptyOfferFormValues()) {
  return render(
    <OfferForm
      mode="create"
      initialValues={initialValues}
      categories={categories}
      merchants={merchants}
    />
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.createOfferAction.mockResolvedValue({
    success: true,
    data: { id: 'o9', slug: 'free-coffee-sample', status: 'DRAFT' },
  });
  mocks.updateOfferAction.mockResolvedValue({
    success: true,
    data: { id: 'o1', slug: 'free-coffee-sample', status: 'DRAFT' },
  });
  mocks.offerLifecycleAction.mockResolvedValue({
    success: true,
    data: { id: 'o1', slug: 'free-coffee-sample', status: 'PUBLISHED' },
  });
});

describe('the commercial basis field', SLOW, () => {
  it('is a required group of three options, none chosen, each with its own line of help', () => {
    createForm();

    const group = screen.getByRole('radiogroup', { name: /Commercial basis/ });
    expect(group).toHaveAttribute('aria-required', 'true');
    const options = within(group).getAllByRole('radio');
    expect(options.map((option) => (option as HTMLInputElement).value)).toEqual([
      'EDITORIAL',
      'AFFILIATE',
      'SPONSORED',
    ]);
    expect(options.every((option) => !(option as HTMLInputElement).checked)).toBe(true);
    for (const option of options) {
      const value = (option as HTMLInputElement).value;
      expect(option).toHaveAccessibleDescription(COMMERCIAL_BASIS_HELP[value]);
    }
  });

  it('refuses a submit with nothing chosen, names the problem on the group and focuses it', async () => {
    const user = userEvent.setup();
    createForm(filled);

    await user.click(screen.getByRole('button', { name: 'Create draft' }));

    const group = await screen.findByRole('radiogroup', { name: /Commercial basis/ });
    await waitFor(() => expect(group).toHaveAttribute('aria-invalid', 'true'));
    expect(group).toHaveAttribute('aria-describedby', 'offer-basis-error');
    expect(document.getElementById('offer-basis-error')).toHaveTextContent(
      'Choose the commercial basis.'
    );
    await waitFor(() => expect(within(group).getAllByRole('radio')[0]).toHaveFocus());
    expect(mocks.createOfferAction).not.toHaveBeenCalled();
  });

  it('sends the chosen basis with the other values', async () => {
    const user = userEvent.setup();
    createForm(filled);

    await user.click(screen.getByRole('radio', { name: /Affiliate/ }));
    await user.click(screen.getByRole('button', { name: 'Create draft' }));

    await waitFor(() => expect(mocks.createOfferAction).toHaveBeenCalledTimes(1));
    expect(mocks.createOfferAction.mock.calls[0][0]).toMatchObject({
      commercialBasis: 'AFFILIATE',
      title: 'Free coffee sample',
    });
  });

  it('starts an existing offer on its saved basis', () => {
    createForm({ ...filled, commercialBasis: 'SPONSORED' });

    expect(screen.getByRole('radio', { name: /Sponsored/ })).toBeChecked();
    expect(screen.getByRole('radio', { name: /Editorial/ })).not.toBeChecked();
  });
});

describe('the card preview and the basis', SLOW, () => {
  it('shows the Sponsored label once Sponsored is chosen, and only then', async () => {
    const user = userEvent.setup();
    createForm(filled);
    const preview = within(screen.getByRole('complementary'));
    expect(preview.queryByRole('note')).toBeNull();

    await user.click(screen.getByRole('radio', { name: /Sponsored/ }));
    expect(preview.getByRole('note', { name: 'Sponsored listing' })).toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: /Affiliate/ }));
    expect(preview.queryByRole('note')).toBeNull();
  });
});

function lifecycle(props: Partial<React.ComponentProps<typeof OfferLifecycleButtons>> = {}) {
  return (
    <DirtyFormProvider>
      <OfferReviewProvider>
        <OfferLifecycleButtons
          offerId="o1"
          offerTitle="Free coffee"
          status="DRAFT"
          reviewToken="tok-1"
          commercialBasis="SPONSORED"
          confirmBasisToPublish
          {...props}
        />
      </OfferReviewProvider>
    </DirtyFormProvider>
  );
}

describe('confirming the basis before publishing a direct-only offer', () => {
  it('holds Publish until the editor ticks the confirmation, and says why', () => {
    render(lifecycle());

    expect(screen.getByRole('button', { name: 'Publish' })).toHaveAttribute(
      'aria-disabled',
      'true'
    );
    expect(
      screen.getByRole('checkbox', { name: /commercial basis is Sponsored/ })
    ).not.toBeChecked();
    expect(screen.getByRole('status')).toHaveTextContent('Confirm the commercial basis above');
  });

  it('does not publish while the confirmation is missing', async () => {
    const user = userEvent.setup();
    render(lifecycle());

    await user.click(screen.getByRole('button', { name: 'Publish' }));

    expect(mocks.offerLifecycleAction).not.toHaveBeenCalled();
  });

  it('publishes with the saved basis once it is confirmed', async () => {
    const user = userEvent.setup();
    render(lifecycle());

    await user.click(screen.getByRole('checkbox', { name: /commercial basis is Sponsored/ }));
    const publish = screen.getByRole('button', { name: 'Publish' });
    expect(publish).not.toHaveAttribute('aria-disabled');
    await user.click(publish);

    await waitFor(() => expect(mocks.offerLifecycleAction).toHaveBeenCalledTimes(1));
    expect(mocks.offerLifecycleAction).toHaveBeenCalledWith('o1', 'publish', 'tok-1', 'SPONSORED');
  });

  it('asks again when the offer has moved on to a new review token', async () => {
    const user = userEvent.setup();
    const { rerender } = render(lifecycle());
    await user.click(screen.getByRole('checkbox', { name: /commercial basis is Sponsored/ }));

    rerender(lifecycle({ reviewToken: 'tok-2', commercialBasis: 'EDITORIAL' }));

    expect(
      screen.getByRole('checkbox', { name: /commercial basis is Editorial/ })
    ).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Publish' })).toHaveAttribute(
      'aria-disabled',
      'true'
    );
  });

  it('lets the editor take the confirmation back', async () => {
    const user = userEvent.setup();
    render(lifecycle());
    const box = screen.getByRole('checkbox', { name: /commercial basis is Sponsored/ });

    await user.click(box);
    await user.click(box);

    expect(box).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Publish' })).toHaveAttribute(
      'aria-disabled',
      'true'
    );
  });

  it('asks for nothing when the offer has a network link or a source URL, and publishes without a basis', async () => {
    const user = userEvent.setup();
    render(lifecycle({ confirmBasisToPublish: false }));

    expect(screen.queryByRole('checkbox')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Publish' }));

    await waitFor(() => expect(mocks.offerLifecycleAction).toHaveBeenCalledTimes(1));
    expect(mocks.offerLifecycleAction).toHaveBeenCalledWith('o1', 'publish', 'tok-1');
  });

  it('shows no confirmation on a published offer, which has no Publish step', () => {
    render(lifecycle({ status: 'PUBLISHED' }));

    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Publish' })).toBeNull();
  });

  it('cannot confirm a basis this UI does not know, so it asks for nothing it cannot send', () => {
    render(lifecycle({ commercialBasis: 'SOMETHING_NEW' }));

    expect(screen.queryByRole('checkbox')).toBeNull();
  });
});
