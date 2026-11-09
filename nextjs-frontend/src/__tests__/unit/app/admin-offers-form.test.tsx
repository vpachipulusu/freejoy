/**
 * The offer, category and merchant forms: validation errors are tied to their fields and take focus, a valid
 * submit calls the server action with the typed values, server messages land on their fields or in a focused
 * summary, and the draft preview card only links to the public page once the offer is published.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// Typing into a long form is slow under coverage instrumentation: the typing-heavy suites get more time.
const SLOW = { timeout: 30_000 };

const mocks = vi.hoisted(() => ({
  createOfferAction: vi.fn(),
  updateOfferAction: vi.fn(),
  createCategoryAction: vi.fn(),
  updateCategoryAction: vi.fn(),
  createMerchantAction: vi.fn(),
  updateMerchantAction: vi.fn(),
  push: vi.fn(),
  refresh: vi.fn(),
  toast: vi.fn(),
}));

vi.mock('@/app/actions/admin/offer-actions', () => ({
  createOfferAction: mocks.createOfferAction,
  updateOfferAction: mocks.updateOfferAction,
}));
vi.mock('@/app/actions/admin/category-actions', () => ({
  createCategoryAction: mocks.createCategoryAction,
  updateCategoryAction: mocks.updateCategoryAction,
}));
vi.mock('@/app/actions/admin/merchant-actions', () => ({
  createMerchantAction: mocks.createMerchantAction,
  updateMerchantAction: mocks.updateMerchantAction,
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mocks.push, refresh: mocks.refresh }),
}));

import { OfferForm } from '@/app/(admin)/admin/offers/_components/offer-form';
import { DirtyFormProvider } from '@/app/(admin)/admin/_components/dirty-form-context';
import { OfferLifecycleButtons } from '@/app/(admin)/admin/offers/_components/offer-lifecycle-buttons';
import { CategoryForm } from '@/app/(admin)/admin/categories/category-form';
import { MerchantForm } from '@/app/(admin)/admin/merchants/merchant-form';
import { emptyOfferFormValues, type OfferFormValues } from '@/lib/admin-catalogue/offer-schema';
import { emptyCategoryFormValues } from '@/lib/admin-catalogue/category-schema';
import { emptyMerchantFormValues } from '@/lib/admin-catalogue/merchant-schema';

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

function renderForm(props: Partial<React.ComponentProps<typeof OfferForm>> = {}) {
  return render(
    <OfferForm
      mode="create"
      initialValues={emptyOfferFormValues()}
      categories={categories}
      merchants={merchants}
      {...props}
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
});

describe('OfferForm validation', SLOW, () => {
  it('ties each error to its field, announces it, and focuses the first invalid field', async () => {
    const user = userEvent.setup();
    renderForm();

    await user.click(screen.getByRole('button', { name: 'Create draft' }));

    const title = await screen.findByLabelText(/^Title/);
    expect(title).toHaveAttribute('aria-invalid', 'true');
    expect(title).toHaveAttribute('aria-describedby', 'offer-title-error');
    expect(document.getElementById('offer-title-error')).toHaveTextContent('Enter a title.');
    // Field errors are tied to their control by aria-describedby; they are not also live regions.
    expect(document.getElementById('offer-title-error')).not.toHaveAttribute('role');
    await waitFor(() => expect(title).toHaveFocus());
    expect(screen.getByLabelText(/^Category/)).toHaveAttribute('aria-invalid', 'true');
    expect(mocks.createOfferAction).not.toHaveBeenCalled();
  });

  it('keeps the hint and the error both attached to a field', async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(screen.getByLabelText(/^Slug/), 'Bad Slug');
    await user.click(screen.getByRole('button', { name: 'Create draft' }));

    const slug = screen.getByLabelText(/^Slug/);
    await waitFor(() =>
      expect(slug).toHaveAttribute('aria-describedby', 'offer-slug-hint offer-slug-error')
    );
  });

  it('asks for the postage amount only when postage costs money', async () => {
    const user = userEvent.setup();
    renderForm();
    expect(screen.queryByLabelText(/Postage cost/)).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(/^Postage/), 'COST');

    expect(screen.getByLabelText(/Postage cost/)).toBeInTheDocument();
  });

  it('adds and removes steps and focuses the new step', async () => {
    const user = userEvent.setup();
    renderForm();
    await user.click(screen.getByRole('button', { name: 'Add a step' }));

    const title = await screen.findByLabelText(/^Step title/);
    await waitFor(() => expect(title).toHaveFocus());
    expect(screen.getByText('Step 1')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Remove step 1/ }));
    expect(screen.queryByText('Step 1')).not.toBeInTheDocument();
  });
});

describe('OfferForm submit', SLOW, () => {
  it('creates a draft with the typed values and opens its edit page', async () => {
    const user = userEvent.setup();
    renderForm({ initialValues: filled });

    await user.click(screen.getByRole('button', { name: 'Create draft' }));

    await waitFor(() => expect(mocks.createOfferAction).toHaveBeenCalledTimes(1));
    expect(mocks.createOfferAction.mock.calls[0][0]).toMatchObject({
      title: 'Free coffee sample',
      categoryId: 'c1',
      merchantId: 'm1',
      offerType: 'FREE_SAMPLE',
    });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/admin/offers/o9'));
    expect(mocks.toast).toHaveBeenCalledWith({
      title: 'Draft created',
      description: 'Free coffee sample',
    });
  });

  it('puts a server field message on its field and focuses it', async () => {
    mocks.createOfferAction.mockResolvedValue({
      success: false,
      error: 'Check the highlighted fields and try again.',
      fieldErrors: { sourceUrl: 'Enter a full https:// address.' },
    });
    const user = userEvent.setup();
    renderForm({ initialValues: filled });

    await user.click(screen.getByRole('button', { name: 'Create draft' }));

    const source = await screen.findByLabelText(/^Source URL/);
    await waitFor(() => expect(source).toHaveFocus());
    expect(source).toHaveAttribute('aria-invalid', 'true');
    expect(document.getElementById('offer-source-url-error')).toHaveTextContent(
      'Enter a full https:// address.'
    );
    expect(screen.getByText('Check the highlighted fields and try again.')).toBeInTheDocument();
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it('focuses a summary for a failure that belongs to no field', async () => {
    mocks.createOfferAction.mockResolvedValue({
      success: false,
      error: 'That slug is already in use. Choose another.',
    });
    const user = userEvent.setup();
    renderForm({ initialValues: filled });

    await user.click(screen.getByRole('button', { name: 'Create draft' }));

    const summary = await screen.findByText('That slug is already in use. Choose another.');
    await waitFor(() => expect(summary.closest('[role="group"]')).toHaveFocus());
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it('shows a curated message and focuses it when the action call itself throws', async () => {
    mocks.createOfferAction.mockRejectedValue(new Error('SECRET network detail'));
    const user = userEvent.setup();
    renderForm({ initialValues: filled });

    await user.click(screen.getByRole('button', { name: 'Create draft' }));

    const summary = await screen.findByText('Something went wrong. Please try again.');
    await waitFor(() => expect(summary.closest('[role="group"]')).toHaveFocus());
    expect(screen.queryByText(/SECRET/)).not.toBeInTheDocument();
  });

  it('marks required fields for assistive technology', () => {
    renderForm();
    expect(screen.getByLabelText(/^Title/)).toHaveAttribute('aria-required', 'true');
    expect(screen.getByLabelText(/^Slug/)).not.toHaveAttribute('aria-required');
  });

  it('moves focus to the next step, or to Add a step, when a step is removed', async () => {
    const user = userEvent.setup();
    renderForm({
      initialValues: {
        ...filled,
        steps: [
          { title: 'One', detail: 'a' },
          { title: 'Two', detail: 'b' },
        ],
      },
    });
    await user.click(screen.getByRole('button', { name: /Remove step 1/ }));
    await waitFor(() => expect(screen.getByLabelText(/^Step title/)).toHaveFocus());
    expect(screen.getByLabelText(/^Step title/)).toHaveValue('Two');
    await user.click(screen.getByRole('button', { name: /Remove step 1/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add a step' })).toHaveFocus());
  });

  it('clears the saved message once the form is edited again', async () => {
    const user = userEvent.setup();
    renderForm({
      mode: 'edit',
      offerId: 'o1',
      status: 'DRAFT',
      initialValues: { ...filled, slug: 'free-coffee-sample' },
    });
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByRole('status')).toBeInTheDocument();
    await user.type(screen.getByLabelText(/^Title/), 'x');
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
  });

  it('saves an edit, announces it and moves focus to the message', async () => {
    const user = userEvent.setup();
    renderForm({
      mode: 'edit',
      offerId: 'o1',
      status: 'DRAFT',
      initialValues: { ...filled, slug: 'free-coffee-sample' },
    });

    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    const saved = await screen.findByRole('status');
    expect(saved).toHaveTextContent('Changes saved.');
    await waitFor(() => expect(saved).toHaveFocus());
    expect(mocks.updateOfferAction).toHaveBeenCalledWith(
      'o1',
      expect.objectContaining({ title: 'Free coffee sample' })
    );
    expect(mocks.refresh).toHaveBeenCalled();
    expect(screen.getByLabelText(/^Slug/)).toHaveAttribute('readonly');
  });

  it('shows an archived offer without a save button and with every field disabled', () => {
    renderForm({
      mode: 'edit',
      offerId: 'o1',
      status: 'ARCHIVED',
      initialValues: filled,
      readOnly: true,
    });
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^Title/)).toBeDisabled();
  });
});

describe('form footer', () => {
  it('names the failure group and does not also make it a live region', async () => {
    mocks.createOfferAction.mockResolvedValue({ success: false, error: 'Nope for now.' });
    const user = userEvent.setup();
    renderForm({ initialValues: filled });
    await user.click(screen.getByRole('button', { name: 'Create draft' }));

    const group = await screen.findByRole('group', { name: 'Could not save' });
    expect(group).toHaveTextContent('Nope for now.');
    expect(group).not.toHaveAttribute('role', 'alert');
    await waitFor(() => expect(group).toHaveFocus());
  });

  it('ignores a second submit while the first is in flight (double click)', async () => {
    let finish: (value: unknown) => void = () => undefined;
    mocks.createOfferAction.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const user = userEvent.setup();
    renderForm({ initialValues: filled });

    const submit = screen.getByRole('button', { name: 'Create draft' });
    await user.dblClick(submit);
    await waitFor(() => expect(mocks.createOfferAction).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('button', { name: 'Saving…' })).toHaveAttribute(
      'aria-disabled',
      'true'
    );
    expect(screen.getByRole('button', { name: 'Saving…' })).not.toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Saving…' }));
    expect(mocks.createOfferAction).toHaveBeenCalledTimes(1);

    finish({ success: true, data: { id: 'o9', slug: 's', status: 'DRAFT' } });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledTimes(1));
  });

  it('warns about unsaved changes, and only while there are some', async () => {
    const user = userEvent.setup();
    renderForm({
      mode: 'edit',
      offerId: 'o1',
      status: 'DRAFT',
      initialValues: { ...filled, slug: 'free-coffee-sample' },
    });
    const unload = () => {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };

    expect(screen.queryByText('You have unsaved changes.')).not.toBeInTheDocument();
    expect(unload()).toBe(false);

    await user.type(screen.getByLabelText(/^Title/), 'x');
    expect(await screen.findByText('You have unsaved changes.')).toBeInTheDocument();
    expect(unload()).toBe(true);

    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(screen.queryByText('You have unsaved changes.')).not.toBeInTheDocument()
    );
    expect(unload()).toBe(false);
  });

  it('warns on a new offer too once it has been typed into', async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(screen.getByLabelText(/^Title/), 'Free tea');
    expect(await screen.findByText('You have unsaved changes.')).toBeInTheDocument();
  });
});

describe('preview card', () => {
  it('updates as the title is typed and says a new offer is not public', async () => {
    const user = userEvent.setup();
    renderForm();
    const preview = screen.getByRole('complementary', { name: 'Card preview' });
    expect(within(preview).getByText('Title not written yet')).toBeInTheDocument();
    expect(within(preview).getByText(/Not saved yet/)).toBeInTheDocument();

    await user.type(screen.getByLabelText(/^Title/), 'Free tea');

    expect(within(preview).getByText('Free tea')).toBeInTheDocument();
    expect(within(preview).queryByRole('link')).not.toBeInTheDocument();
  });

  it('links to the live page only for a published offer', () => {
    const { unmount } = renderForm({
      mode: 'edit',
      offerId: 'o1',
      status: 'PUBLISHED',
      initialValues: { ...filled, slug: 'free-coffee-sample' },
    });
    expect(screen.getByRole('link', { name: /View the live page/ })).toHaveAttribute(
      'href',
      '/freebies/free-coffee-sample'
    );
    unmount();

    renderForm({
      mode: 'edit',
      offerId: 'o1',
      status: 'DRAFT',
      initialValues: { ...filled, slug: 'free-coffee-sample' },
    });
    expect(screen.queryByRole('link', { name: /live page/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Not public/)).toBeInTheDocument();
  });

  it('prices postage in pounds from the typed amount', async () => {
    const user = userEvent.setup();
    renderForm({ initialValues: { ...filled, postageKind: 'COST', postageAmount: '' } });
    const preview = screen.getByRole('complementary', { name: 'Card preview' });
    expect(within(preview).getByText('Postage costs money')).toBeInTheDocument();
    await user.type(screen.getByLabelText(/Postage cost/), '2.5');
    expect(within(preview).getByText('Postage £2.50')).toBeInTheDocument();
  });
});

describe('CategoryForm', SLOW, () => {
  it('validates and then creates a category', async () => {
    mocks.createCategoryAction.mockResolvedValue({
      success: true,
      data: { id: 'c9', slug: 'pets', enabled: true },
    });
    const user = userEvent.setup();
    render(<CategoryForm mode="create" initialValues={emptyCategoryFormValues()} />);

    await user.click(screen.getByRole('button', { name: 'Create category' }));
    expect(await screen.findByLabelText(/^Name/)).toHaveAttribute('aria-invalid', 'true');
    expect(mocks.createCategoryAction).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText(/^Name/), 'Pets');
    await user.type(screen.getByLabelText(/^Short name/), 'Pets');
    await user.type(screen.getByLabelText(/^Description/), 'Treats for pets.');
    await user.type(screen.getByLabelText(/^Introduction/), 'Samples for your pet.');
    await user.click(screen.getByRole('button', { name: 'Create category' }));

    await waitFor(() => expect(mocks.createCategoryAction).toHaveBeenCalledTimes(1));
    expect(mocks.createCategoryAction.mock.calls[0][0]).toMatchObject({
      name: 'Pets',
      sortOrder: '0',
      enabled: true,
    });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/admin/categories'));
  });

  it('keeps the slug read only when editing and shows a server message', async () => {
    mocks.updateCategoryAction.mockResolvedValue({
      success: false,
      error: 'You do not have permission to do that',
    });
    const user = userEvent.setup();
    render(
      <CategoryForm
        mode="edit"
        categoryId="c1"
        initialValues={{
          ...emptyCategoryFormValues(),
          name: 'Beauty',
          slug: 'beauty',
          shortName: 'Beauty',
          description: 'Skincare.',
          intro: 'Try it.',
        }}
      />
    );
    expect(screen.getByLabelText(/^Slug/)).toHaveAttribute('readonly');
    expect(screen.queryByLabelText(/^Enabled/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    const summary = await screen.findByText('You do not have permission to do that');
    await waitFor(() => expect(summary.closest('[role="group"]')).toHaveFocus());
  });
});

describe('MerchantForm', SLOW, () => {
  it('rejects a non-https website, then creates a merchant', async () => {
    mocks.createMerchantAction.mockResolvedValue({
      success: true,
      data: { id: 'm9', slug: 'acme', active: true },
    });
    const user = userEvent.setup();
    render(<MerchantForm mode="create" initialValues={emptyMerchantFormValues()} />);

    await user.type(screen.getByLabelText(/^Name/), 'Acme');
    await user.type(screen.getByLabelText(/^Website/), 'http://acme.test');
    await user.click(screen.getByRole('button', { name: 'Create merchant' }));
    expect(await screen.findByLabelText(/^Website/)).toHaveAttribute('aria-invalid', 'true');
    expect(mocks.createMerchantAction).not.toHaveBeenCalled();

    await user.clear(screen.getByLabelText(/^Website/));
    await user.type(screen.getByLabelText(/^Website/), 'https://acme.example');
    await user.click(screen.getByRole('button', { name: 'Create merchant' }));

    await waitFor(() => expect(mocks.createMerchantAction).toHaveBeenCalledTimes(1));
    expect(mocks.createMerchantAction.mock.calls[0][0]).toMatchObject({
      name: 'Acme',
      website: 'https://acme.example',
      active: true,
    });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/admin/merchants'));
  });

  it('saves an edit and announces it', async () => {
    mocks.updateMerchantAction.mockResolvedValue({
      success: true,
      data: { id: 'm1', slug: 'acme', active: true },
    });
    const user = userEvent.setup();
    render(
      <MerchantForm
        mode="edit"
        merchantId="m1"
        initialValues={{ ...emptyMerchantFormValues(), name: 'Acme', slug: 'acme' }}
      />
    );
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Changes saved.');
    expect(mocks.updateMerchantAction).toHaveBeenCalledWith(
      'm1',
      expect.objectContaining({ name: 'Acme' })
    );
  });
});

describe('edit page: the form and the status buttons together', () => {
  it('holds the buttons back while the form has unsaved edits and frees them after a save', async () => {
    const user = userEvent.setup();
    render(
      <DirtyFormProvider>
        <OfferLifecycleButtons
          offerId="o1"
          offerTitle="Free coffee"
          reviewToken="tok-1"
          status="DRAFT"
        />
        <OfferForm
          mode="edit"
          offerId="o1"
          status="DRAFT"
          initialValues={{ ...filled, slug: 'free-coffee-sample' }}
          categories={categories}
          merchants={merchants}
        />
      </DirtyFormProvider>
    );
    const publish = screen.getByRole('button', { name: 'Publish' });
    expect(publish).not.toHaveAttribute('aria-disabled');

    await user.type(screen.getByLabelText(/^Title/), 'x');
    await waitFor(() => expect(publish).toHaveAttribute('aria-disabled', 'true'));

    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(publish).not.toHaveAttribute('aria-disabled'));
  });
});

describe('category and merchant forms: one submit at a time', SLOW, () => {
  it('a double click on Create category calls the action once', async () => {
    let finish: (value: unknown) => void = () => undefined;
    mocks.createCategoryAction.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const user = userEvent.setup();
    render(
      <CategoryForm
        mode="create"
        initialValues={{
          ...emptyCategoryFormValues(),
          name: 'Pets',
          shortName: 'Pets',
          description: 'Treats.',
          intro: 'Samples.',
        }}
      />
    );
    await user.dblClick(screen.getByRole('button', { name: 'Create category' }));
    await waitFor(() => expect(mocks.createCategoryAction).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('button', { name: 'Saving…' })).toHaveAttribute(
      'aria-disabled',
      'true'
    );
    finish({ success: true, data: { id: 'c9', slug: 'pets', enabled: true } });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledTimes(1));
  });

  it('a double click on Create merchant calls the action once', async () => {
    let finish: (value: unknown) => void = () => undefined;
    mocks.createMerchantAction.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const user = userEvent.setup();
    render(
      <MerchantForm mode="create" initialValues={{ ...emptyMerchantFormValues(), name: 'Acme' }} />
    );
    await user.dblClick(screen.getByRole('button', { name: 'Create merchant' }));
    await waitFor(() => expect(mocks.createMerchantAction).toHaveBeenCalledTimes(1));
    finish({ success: true, data: { id: 'm9', slug: 'acme', active: true } });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledTimes(1));
  });

  it('warn about unsaved changes while dirty', async () => {
    const user = userEvent.setup();
    render(<MerchantForm mode="create" initialValues={emptyMerchantFormValues()} />);
    expect(screen.queryByText('You have unsaved changes.')).not.toBeInTheDocument();
    await user.type(screen.getByLabelText(/^Name/), 'A');
    expect(await screen.findByText('You have unsaved changes.')).toBeInTheDocument();
  });
});
