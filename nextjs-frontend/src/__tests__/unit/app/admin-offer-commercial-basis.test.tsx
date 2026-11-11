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
    expect(preview.getByRole('note')).toHaveTextContent('Sponsored listing');

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
    expect(screen.getByRole('status')).toHaveTextContent('Tick the confirmation to publish.');
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

describe('accessible names and descriptions of the basis controls', SLOW, () => {
  it('names each radio by its option alone and describes it by its help line, read once', () => {
    createForm();

    for (const value of ['Editorial', 'Affiliate', 'Sponsored']) {
      const radio = screen.getByRole('radio', { name: value });
      expect(radio).toHaveAccessibleDescription(COMMERCIAL_BASIS_HELP[value.toUpperCase()]);
    }
  });

  it('adds the group error to the description of every radio while there is one', async () => {
    const user = userEvent.setup();
    createForm(filled);

    await user.click(screen.getByRole('button', { name: 'Create draft' }));

    await waitFor(() =>
      expect(screen.getByRole('radio', { name: 'Editorial' })).toHaveAccessibleDescription(
        /Choose the commercial basis\./
      )
    );
    for (const radio of screen.getAllByRole('radio')) {
      expect(radio).toHaveAccessibleDescription(/Choose the commercial basis\./);
    }
  });

  it('names the confirmation checkbox exactly and describes it by the help and the reason', () => {
    render(lifecycle());

    const box = screen.getByRole('checkbox', {
      name: "I confirm this listing's commercial basis is Sponsored",
    });
    expect(box).toHaveAccessibleDescription(/only active links/);
    expect(box).toHaveAccessibleDescription(/Visitors see a "Sponsored" label/);
  });
});

describe('the publish control while it waits for the confirmation', () => {
  it('describes the held Publish button by the status text, which mentions the confirmation', () => {
    render(lifecycle());

    expect(screen.getByRole('button', { name: 'Publish' })).toHaveAccessibleDescription(
      /Tick the confirmation to publish\./
    );
  });

  it('puts the confirmation before the status buttons in the reading order', () => {
    render(lifecycle());

    const box = screen.getByRole('checkbox');
    const publish = screen.getByRole('button', { name: 'Publish' });
    expect(box.compareDocumentPosition(publish) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('moves focus to the confirmation when the held Publish is pressed', async () => {
    const user = userEvent.setup();
    render(lifecycle());

    await user.click(screen.getByRole('button', { name: 'Publish' }));

    expect(screen.getByRole('checkbox')).toHaveFocus();
    expect(mocks.offerLifecycleAction).not.toHaveBeenCalled();
  });

  it('does not describe Publish by the status once it is ticked and free to go', async () => {
    const user = userEvent.setup();
    render(lifecycle());

    await user.click(screen.getByRole('checkbox'));

    expect(screen.getByRole('button', { name: 'Publish' })).not.toHaveAttribute('aria-describedby');
  });

  it('holds Publish, and says what to do, when the saved basis is one this page does not know', async () => {
    const user = userEvent.setup();
    render(lifecycle({ commercialBasis: 'SOMETHING_NEW' }));
    const publish = screen.getByRole('button', { name: 'Publish' });

    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(publish).toHaveAttribute('aria-disabled', 'true');
    expect(publish).toHaveAccessibleDescription(
      'Choose the commercial basis in the form and save first.'
    );
    await user.click(publish);
    expect(mocks.offerLifecycleAction).not.toHaveBeenCalled();
  });

  it('keeps the confirmation ticked after a refusal that leaves the review token as it was', async () => {
    const user = userEvent.setup();
    mocks.offerLifecycleAction.mockResolvedValue({
      success: false,
      error: 'This offer cannot be published yet: it has not been verified.',
    });
    render(lifecycle());
    await user.click(screen.getByRole('checkbox'));

    await user.click(screen.getByRole('button', { name: 'Publish' }));

    await waitFor(() => expect(mocks.offerLifecycleAction).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('checkbox')).toBeChecked();
  });
});

function editForm(values: Partial<OfferFormValues> = {}) {
  return (
    <OfferForm
      mode="edit"
      offerId="o1"
      status="DRAFT"
      initialValues={{
        ...filled,
        slug: 'free-coffee-sample',
        commercialBasis: 'EDITORIAL',
        ...values,
      }}
      categories={categories}
      merchants={merchants}
    />
  );
}

describe('editing the basis of a saved offer', SLOW, () => {
  it('follows the saved basis when the page reloads with a new one and the field is untouched', () => {
    const { rerender } = render(editForm());
    expect(screen.getByRole('radio', { name: 'Editorial' })).toBeChecked();

    rerender(editForm({ commercialBasis: 'SPONSORED' }));

    expect(screen.getByRole('radio', { name: 'Sponsored' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Editorial' })).not.toBeChecked();
  });

  it('keeps the choice the editor made when the page reloads with another basis', async () => {
    const user = userEvent.setup();
    const { rerender } = render(editForm());
    await user.click(screen.getByRole('radio', { name: 'Affiliate' }));

    rerender(editForm({ commercialBasis: 'SPONSORED' }));

    expect(screen.getByRole('radio', { name: 'Affiliate' })).toBeChecked();
  });

  it('puts the error of a refused basis on its field and focuses the first radio', async () => {
    const user = userEvent.setup();
    mocks.updateOfferAction.mockResolvedValue({
      success: false,
      error: 'Check the highlighted fields and try again.',
      fieldErrors: { commercialBasis: 'Choose the commercial basis.' },
    });
    render(editForm());
    await user.click(screen.getByRole('radio', { name: 'Affiliate' }));

    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(screen.getByRole('radio', { name: 'Editorial' })).toHaveFocus());
    expect(screen.getByRole('radiogroup', { name: /Commercial basis/ })).toHaveAttribute(
      'aria-invalid',
      'true'
    );
  });
});

describe('publishing a retailer submission whose basis is only a placeholder', () => {
  const placeholder = (props: Partial<React.ComponentProps<typeof OfferLifecycleButtons>> = {}) =>
    lifecycle({ basisIsPlaceholder: true, commercialBasis: 'SPONSORED', ...props });
  const group = () => screen.getByRole('radiogroup', { name: /Commercial basis/ });

  it('asks for a choice with nothing selected, and never offers the placeholder as the current basis', () => {
    render(placeholder());

    const options = within(group()).getAllByRole('radio');
    expect(options.map((option) => (option as HTMLInputElement).value)).toEqual([
      'EDITORIAL',
      'AFFILIATE',
      'SPONSORED',
    ]);
    expect(options.every((option) => !(option as HTMLInputElement).checked)).toBe(true);
    for (const option of options) {
      expect(option).toHaveAccessibleDescription(
        COMMERCIAL_BASIS_HELP[(option as HTMLInputElement).value]
      );
    }
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.getByText(/nobody has chosen yet how it is paid for/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/basis is Sponsored/i);
  });

  it('holds Publish until a basis is chosen, says why, and sends the editor to the choice', async () => {
    const user = userEvent.setup();
    render(placeholder());
    const publish = screen.getByRole('button', { name: 'Publish' });

    expect(publish).toHaveAttribute('aria-disabled', 'true');
    expect(publish).toHaveAccessibleDescription('Choose the commercial basis to publish.');
    await user.click(publish);

    expect(within(group()).getAllByRole('radio')[0]).toHaveFocus();
    expect(mocks.offerLifecycleAction).not.toHaveBeenCalled();
  });

  it.each(['Editorial', 'Affiliate', 'Sponsored'])(
    'publishes with the %s the editor chose',
    async (name) => {
      const user = userEvent.setup();
      render(placeholder());

      await user.click(within(group()).getByRole('radio', { name }));
      const publish = screen.getByRole('button', { name: 'Publish' });
      expect(publish).not.toHaveAttribute('aria-disabled');
      await user.click(publish);

      await waitFor(() => expect(mocks.offerLifecycleAction).toHaveBeenCalledTimes(1));
      expect(mocks.offerLifecycleAction).toHaveBeenCalledWith(
        'o1',
        'publish',
        'tok-1',
        name.toUpperCase()
      );
    }
  );

  it('lets the editor change the choice before publishing', async () => {
    const user = userEvent.setup();
    render(placeholder());

    await user.click(within(group()).getByRole('radio', { name: 'Sponsored' }));
    await user.click(within(group()).getByRole('radio', { name: 'Editorial' }));
    await user.click(screen.getByRole('button', { name: 'Publish' }));

    await waitFor(() => expect(mocks.offerLifecycleAction).toHaveBeenCalledTimes(1));
    expect(mocks.offerLifecycleAction.mock.calls[0][3]).toBe('EDITORIAL');
  });

  it('asks again when the offer has moved on to a new review token', async () => {
    const user = userEvent.setup();
    const { rerender } = render(placeholder());
    await user.click(within(group()).getByRole('radio', { name: 'Affiliate' }));

    rerender(placeholder({ reviewToken: 'tok-2' }));

    expect(
      within(group())
        .getAllByRole('radio')
        .every((option) => !(option as HTMLInputElement).checked)
    ).toBe(true);
    expect(screen.getByRole('button', { name: 'Publish' })).toHaveAttribute(
      'aria-disabled',
      'true'
    );
  });

  it('asks for nothing when the API does not need a confirmation, or the offer is live', () => {
    const { rerender } = render(placeholder({ confirmBasisToPublish: false }));
    expect(screen.queryByRole('radiogroup')).toBeNull();

    rerender(placeholder({ status: 'PUBLISHED' }));
    expect(screen.queryByRole('radiogroup')).toBeNull();
  });
});

describe('editing an offer whose basis is only a placeholder', SLOW, () => {
  const placeholderForm = () =>
    render(
      <OfferForm
        mode="edit"
        offerId="o1"
        status="DRAFT"
        initialValues={{ ...filled, slug: 'free-coffee-sample', commercialBasis: '' }}
        categories={categories}
        merchants={merchants}
        basisIsPlaceholder
      />
    );

  it('shows nothing chosen, says so, and does not mark the field required', () => {
    placeholderForm();

    const group = screen.getByRole('radiogroup', { name: /Commercial basis/ });
    expect(
      within(group)
        .getAllByRole('radio')
        .every((option) => !(option as HTMLInputElement).checked)
    ).toBe(true);
    expect(group).not.toHaveAttribute('aria-required');
    expect(group).toHaveAccessibleDescription(/Not chosen by an editor yet/);
    expect(screen.getByText(/Not chosen by an editor yet/)).toBeInTheDocument();
  });

  it('shows no Sponsored label in the preview for it', () => {
    placeholderForm();

    expect(within(screen.getByRole('complementary')).queryByRole('note')).toBeNull();
  });

  it('saves other changes without a basis, and sends none', async () => {
    const user = userEvent.setup();
    placeholderForm();

    await user.clear(screen.getByLabelText(/^Title/));
    await user.type(screen.getByLabelText(/^Title/), 'A new title');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(mocks.updateOfferAction).toHaveBeenCalledTimes(1));
    expect(mocks.updateOfferAction.mock.calls[0][1]).toMatchObject({
      title: 'A new title',
      commercialBasis: '',
    });
  });

  it('sends the basis the editor picked', async () => {
    const user = userEvent.setup();
    placeholderForm();

    await user.click(screen.getByRole('radio', { name: 'Affiliate' }));
    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(mocks.updateOfferAction).toHaveBeenCalledTimes(1));
    expect(mocks.updateOfferAction.mock.calls[0][1]).toMatchObject({
      commercialBasis: 'AFFILIATE',
    });
  });

  it('still requires a basis on an offer that is not a placeholder', async () => {
    const user = userEvent.setup();
    render(editForm({ commercialBasis: '' }));

    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(await screen.findByText('Choose the commercial basis.')).toBeInTheDocument();
    expect(mocks.updateOfferAction).not.toHaveBeenCalled();
  });
});
