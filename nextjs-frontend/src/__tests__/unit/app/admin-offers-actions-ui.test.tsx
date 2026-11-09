/**
 * The offer row menu, the edit page's lifecycle buttons and the category/merchant toggle: which steps each
 * status offers, the archive confirmation, and how an outcome is announced.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mocks = vi.hoisted(() => ({
  offerLifecycleAction: vi.fn(),
  setCategoryEnabledAction: vi.fn(),
  setMerchantActiveAction: vi.fn(),
  toast: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock('@/app/actions/admin/offer-actions', () => ({
  offerLifecycleAction: mocks.offerLifecycleAction,
}));
vi.mock('@/app/actions/admin/category-actions', () => ({
  setCategoryEnabledAction: mocks.setCategoryEnabledAction,
}));
vi.mock('@/app/actions/admin/merchant-actions', () => ({
  setMerchantActiveAction: mocks.setMerchantActiveAction,
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: mocks.refresh, push: vi.fn() }),
}));

import { OfferRowActions } from '@/app/(admin)/admin/offers/_components/offer-row-actions';
import { OfferLifecycleButtons } from '@/app/(admin)/admin/offers/_components/offer-lifecycle-buttons';
import {
  DirtyFormProvider,
  useReportFormDirty,
} from '@/app/(admin)/admin/_components/dirty-form-context';
import { StateToggleButton } from '@/app/(admin)/admin/_components/state-toggle-button';

const ok = { success: true as const, data: {} };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.offerLifecycleAction.mockResolvedValue(ok);
  mocks.setCategoryEnabledAction.mockResolvedValue(ok);
  mocks.setMerchantActiveAction.mockResolvedValue(ok);
});

async function openMenu(user: ReturnType<typeof userEvent.setup>, title: string) {
  await user.click(screen.getByRole('button', { name: `Open menu for ${title}` }));
  return screen.findByRole('menu');
}

describe('OfferRowActions', () => {
  it.each([
    ['DRAFT', ['Edit', 'Mark verified', 'Publish', 'Archive']],
    ['PUBLISHED', ['Edit', 'Mark verified', 'Unpublish', 'Expire', 'Archive']],
    ['EXPIRED', ['Edit', 'Mark verified', 'Publish', 'Archive']],
    ['ARCHIVED', ['Edit']],
  ])('offers the right steps for a %s offer', async (status, expected) => {
    const user = userEvent.setup();
    render(
      <OfferRowActions
        offerId="o1"
        offerTitle="Free coffee"
        reviewToken="tok-1"
        status={status as 'DRAFT'}
      />
    );

    const menu = await openMenu(user, 'Free coffee');

    expect(
      within(menu)
        .getAllByRole('menuitem')
        .map((item) => item.textContent)
    ).toEqual(expected);
    expect(within(menu).getByRole('menuitem', { name: 'Edit' })).toHaveAttribute(
      'href',
      '/admin/offers/o1'
    );
  });

  it('runs a step through the server action, announces it and refreshes', async () => {
    const user = userEvent.setup();
    render(
      <OfferRowActions offerId="o1" offerTitle="Free coffee" reviewToken="tok-1" status="DRAFT" />
    );

    await openMenu(user, 'Free coffee');
    await user.click(screen.getByRole('menuitem', { name: 'Mark verified' }));

    await waitFor(() =>
      expect(mocks.offerLifecycleAction).toHaveBeenCalledWith('o1', 'verify', 'tok-1')
    );
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        title: 'Offer marked as verified',
        description: 'Free coffee',
      })
    );
    expect(mocks.refresh).toHaveBeenCalled();
  });

  it('shows the curated reason in a destructive toast when a step is refused', async () => {
    mocks.offerLifecycleAction.mockResolvedValue({
      success: false,
      error: 'This offer cannot be published yet: it has not been verified.',
    });
    const user = userEvent.setup();
    render(
      <OfferRowActions offerId="o1" offerTitle="Free coffee" reviewToken="tok-1" status="DRAFT" />
    );

    await openMenu(user, 'Free coffee');
    await user.click(screen.getByRole('menuitem', { name: 'Publish' }));

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        variant: 'destructive',
        title: 'Could not publish',
        description: 'This offer cannot be published yet: it has not been verified.',
      })
    );
  });

  it('asks before archiving, and archives only after confirmation', async () => {
    const user = userEvent.setup();
    render(
      <OfferRowActions
        offerId="o1"
        offerTitle="Free coffee"
        reviewToken="tok-1"
        status="PUBLISHED"
      />
    );

    await openMenu(user, 'Free coffee');
    await user.click(screen.getByRole('menuitem', { name: 'Archive' }));

    const dialog = await screen.findByRole('dialog', { name: 'Archive this offer?' });
    expect(mocks.offerLifecycleAction).not.toHaveBeenCalled();
    expect(within(dialog).getByText(/cannot be undone/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(mocks.offerLifecycleAction).not.toHaveBeenCalled();

    await openMenu(user, 'Free coffee');
    await user.click(screen.getByRole('menuitem', { name: 'Archive' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Archive offer' })
    );
    await waitFor(() =>
      expect(mocks.offerLifecycleAction).toHaveBeenCalledWith('o1', 'archive', 'tok-1')
    );
  });
});

describe('a publish or verify refused because the offer changed', () => {
  const CHANGED = 'This offer changed since you opened it. Review it again.';

  it.each(['Publish', 'Mark verified'])(
    'the row menu shows the curated message and reloads the offer data (%s)',
    async (label) => {
      mocks.offerLifecycleAction.mockResolvedValue({ success: false, error: CHANGED });
      const user = userEvent.setup();
      render(
        <OfferRowActions offerId="o1" offerTitle="Free coffee" reviewToken="tok-1" status="DRAFT" />
      );

      await openMenu(user, 'Free coffee');
      await user.click(screen.getByRole('menuitem', { name: label }));

      await waitFor(() =>
        expect(mocks.toast).toHaveBeenCalledWith(
          expect.objectContaining({ variant: 'destructive', description: CHANGED })
        )
      );
      expect(mocks.refresh).toHaveBeenCalled();
    }
  );

  it('the edit page buttons send the token they were given, and the new one after a refresh', async () => {
    mocks.offerLifecycleAction.mockResolvedValue({ success: false, error: CHANGED });
    const user = userEvent.setup();
    const { rerender } = render(
      <OfferLifecycleButtons
        offerId="o1"
        offerTitle="Free coffee"
        status="DRAFT"
        reviewToken="old-token"
      />
    );

    await user.click(screen.getByRole('button', { name: 'Publish' }));
    await waitFor(() =>
      expect(mocks.offerLifecycleAction).toHaveBeenLastCalledWith('o1', 'publish', 'old-token')
    );
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());

    rerender(
      <OfferLifecycleButtons
        offerId="o1"
        offerTitle="Free coffee"
        status="DRAFT"
        reviewToken="new-token"
      />
    );
    await user.click(screen.getByRole('button', { name: 'Publish' }));

    await waitFor(() =>
      expect(mocks.offerLifecycleAction).toHaveBeenLastCalledWith('o1', 'publish', 'new-token')
    );
  });
});

describe('a server action call that throws', () => {
  it('toasts a curated failure instead of crashing', async () => {
    mocks.offerLifecycleAction.mockRejectedValue(new Error('SECRET'));
    const user = userEvent.setup();
    render(
      <OfferLifecycleButtons
        offerId="o1"
        offerTitle="Free coffee"
        reviewToken="tok-1"
        status="DRAFT"
      />
    );
    await user.click(screen.getByRole('button', { name: 'Publish' }));
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        variant: 'destructive',
        title: 'Could not publish',
        description: 'Something went wrong. Please try again.',
      })
    );
  });
});

describe('OfferLifecycleButtons', () => {
  it('shows the steps as buttons in a named group', () => {
    render(
      <OfferLifecycleButtons
        offerId="o1"
        offerTitle="Free coffee"
        reviewToken="tok-1"
        status="PUBLISHED"
      />
    );
    const group = screen.getByRole('group', { name: 'Offer status actions' });
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent)
    ).toEqual(['Mark verified', 'Unpublish', 'Expire', 'Archive']);
  });

  it('publishes through the server action', async () => {
    const user = userEvent.setup();
    render(
      <OfferLifecycleButtons
        offerId="o1"
        offerTitle="Free coffee"
        reviewToken="tok-1"
        status="EXPIRED"
      />
    );
    await user.click(screen.getByRole('button', { name: 'Publish' }));
    await waitFor(() =>
      expect(mocks.offerLifecycleAction).toHaveBeenCalledWith('o1', 'publish', 'tok-1')
    );
  });

  it('says an archived offer cannot change and offers no button', () => {
    render(
      <OfferLifecycleButtons
        offerId="o1"
        offerTitle="Free coffee"
        reviewToken="tok-1"
        status="ARCHIVED"
      />
    );
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByText(/archived and cannot be changed/)).toBeInTheDocument();
  });
});

describe('StateToggleButton', () => {
  it('disables an enabled category and names it for screen readers', async () => {
    const user = userEvent.setup();
    render(<StateToggleButton kind="category" id="c1" name="Beauty" on />);

    await user.click(screen.getByRole('button', { name: 'Disable Beauty' }));

    await waitFor(() => expect(mocks.setCategoryEnabledAction).toHaveBeenCalledWith('c1', false));
    await waitFor(() => expect(mocks.toast).toHaveBeenCalledWith({ title: 'Beauty disabled' }));
    expect(mocks.refresh).toHaveBeenCalled();
  });

  it('activates an inactive merchant', async () => {
    const user = userEvent.setup();
    render(<StateToggleButton kind="merchant" id="m1" name="Acme" on={false} />);

    await user.click(screen.getByRole('button', { name: 'Activate Acme' }));

    await waitFor(() => expect(mocks.setMerchantActiveAction).toHaveBeenCalledWith('m1', true));
    await waitFor(() => expect(mocks.toast).toHaveBeenCalledWith({ title: 'Acme activated' }));
  });

  it('reports a refusal with the curated message', async () => {
    mocks.setCategoryEnabledAction.mockResolvedValue({
      success: false,
      error: 'You do not have permission to do that',
    });
    const user = userEvent.setup();
    render(<StateToggleButton kind="category" id="c1" name="Beauty" on />);

    await user.click(screen.getByRole('button', { name: 'Disable Beauty' }));

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        variant: 'destructive',
        title: 'Could not change Beauty',
        description: 'You do not have permission to do that',
      })
    );
  });
});

/** A promise the test settles by hand, to hold an action in flight. */
function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((done) => (resolve = done));
  return { promise, resolve };
}

describe('while an action is in flight', () => {
  it('the row menu runs the action once, keeps focus on its trigger and announces the wait', async () => {
    const pending = deferred<typeof ok>();
    mocks.offerLifecycleAction.mockReturnValue(pending.promise);
    const user = userEvent.setup();
    render(
      <OfferRowActions offerId="o1" offerTitle="Free coffee" reviewToken="tok-1" status="DRAFT" />
    );

    const trigger = screen.getByRole('button', { name: 'Open menu for Free coffee' });
    await openMenu(user, 'Free coffee');
    await user.click(screen.getByRole('menuitem', { name: 'Publish' }));

    await waitFor(() => expect(trigger).toHaveAttribute('aria-disabled', 'true'));
    expect(trigger).not.toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('Updating Free coffee');

    // A second choice while the first is pending does nothing.
    await user.click(trigger);
    const menu = screen.queryByRole('menu');
    if (menu) await user.click(within(menu).getByRole('menuitem', { name: 'Mark verified' }));
    expect(mocks.offerLifecycleAction).toHaveBeenCalledTimes(1);

    pending.resolve(ok);
    await waitFor(() => expect(trigger).not.toHaveAttribute('aria-disabled'));
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  });

  it('returns focus to the row trigger after the archive confirmation', async () => {
    const user = userEvent.setup();
    render(
      <OfferRowActions
        offerId="o1"
        offerTitle="Free coffee"
        reviewToken="tok-1"
        status="PUBLISHED"
      />
    );
    const trigger = screen.getByRole('button', { name: 'Open menu for Free coffee' });

    await openMenu(user, 'Free coffee');
    await user.click(screen.getByRole('menuitem', { name: 'Archive' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Archive offer' })
    );

    await waitFor(() =>
      expect(mocks.offerLifecycleAction).toHaveBeenCalledWith('o1', 'archive', 'tok-1')
    );
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('the lifecycle buttons run once, stay focused and say what they are doing', async () => {
    const pending = deferred<typeof ok>();
    mocks.offerLifecycleAction.mockReturnValue(pending.promise);
    const user = userEvent.setup();
    render(
      <OfferLifecycleButtons
        offerId="o1"
        offerTitle="Free coffee"
        reviewToken="tok-1"
        status="DRAFT"
      />
    );

    const publish = screen.getByRole('button', { name: 'Publish' });
    await user.dblClick(publish);

    expect(mocks.offerLifecycleAction).toHaveBeenCalledTimes(1);
    expect(publish).toHaveAttribute('aria-disabled', 'true');
    expect(publish).not.toBeDisabled();
    expect(publish).toHaveFocus();
    expect(screen.getByRole('status')).toHaveTextContent('Updating Free coffee');
    await user.click(screen.getByRole('button', { name: 'Mark verified' }));
    expect(mocks.offerLifecycleAction).toHaveBeenCalledTimes(1);

    pending.resolve(ok);
    await waitFor(() => expect(publish).not.toHaveAttribute('aria-disabled'));
  });

  it('the lifecycle buttons return focus to Archive after the confirmation', async () => {
    const user = userEvent.setup();
    render(
      <OfferLifecycleButtons
        offerId="o1"
        offerTitle="Free coffee"
        reviewToken="tok-1"
        status="PUBLISHED"
      />
    );
    const archive = screen.getByRole('button', { name: 'Archive' });

    await user.click(archive);
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Archive offer' })
    );

    await waitFor(() =>
      expect(mocks.offerLifecycleAction).toHaveBeenCalledWith('o1', 'archive', 'tok-1')
    );
    await waitFor(() => expect(archive).toHaveFocus());
  });

  it('the category toggle runs once and keeps focus', async () => {
    const pending = deferred<typeof ok>();
    mocks.setCategoryEnabledAction.mockReturnValue(pending.promise);
    const user = userEvent.setup();
    render(<StateToggleButton kind="category" id="c1" name="Beauty" on />);

    const button = screen.getByRole('button', { name: 'Disable Beauty' });
    await user.dblClick(button);

    expect(mocks.setCategoryEnabledAction).toHaveBeenCalledTimes(1);
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).not.toBeDisabled();
    expect(button).toHaveFocus();
    expect(screen.getByRole('status')).toHaveTextContent('Updating Beauty');

    pending.resolve(ok);
    await waitFor(() => expect(button).not.toHaveAttribute('aria-disabled'));
  });
});

describe('beside a form with unsaved changes', () => {
  function Editor({ dirty }: { dirty: boolean }) {
    function Reporter() {
      useReportFormDirty(dirty);
      return null;
    }
    return (
      <DirtyFormProvider>
        <Reporter />
        <OfferLifecycleButtons
          offerId="o1"
          offerTitle="Free coffee"
          reviewToken="tok-1"
          status="DRAFT"
        />
      </DirtyFormProvider>
    );
  }

  it('waits, says why, and runs nothing', async () => {
    const user = userEvent.setup();
    render(<Editor dirty />);

    const publish = screen.getByRole('button', { name: 'Publish' });
    expect(publish).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('status')).toHaveTextContent('Save or discard your changes first');
    await user.click(publish);
    expect(mocks.offerLifecycleAction).not.toHaveBeenCalled();
  });

  it('works as usual when the form is clean', async () => {
    const user = userEvent.setup();
    render(<Editor dirty={false} />);

    const publish = screen.getByRole('button', { name: 'Publish' });
    expect(publish).not.toHaveAttribute('aria-disabled');
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
    await user.click(publish);
    await waitFor(() =>
      expect(mocks.offerLifecycleAction).toHaveBeenCalledWith('o1', 'publish', 'tok-1')
    );
  });
});
