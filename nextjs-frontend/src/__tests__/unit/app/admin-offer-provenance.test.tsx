/**
 * The offer page's Provenance panel and its "Dismiss source change" button: what the panel shows, when the
 * button exists, the confirmation (keyboard, focus, double presses), how an outcome is announced (the same wiring
 * as Mark verified: a toast, the review state, then router.refresh()) and where focus goes when the button is
 * replaced after a dismissal.
 */
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupUser } from '@/__tests__/unit/helpers/user';

const mocks = vi.hoisted(() => ({
  dismissSourceChangeAction: vi.fn(),
  toast: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock('@/app/actions/admin/offer-actions', () => ({
  dismissSourceChangeAction: mocks.dismissSourceChangeAction,
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: mocks.refresh, push: vi.fn() }),
}));

import { OfferProvenancePanel } from '@/app/(admin)/admin/offers/_components/offer-provenance-panel';
import {
  OfferReviewProvider,
  useOfferReview,
  useReportLoadedToken,
} from '@/app/(admin)/admin/offers/_components/offer-review-context';
import {
  DirtyFormProvider,
  useReportFormBusy,
  useReportFormDirty,
} from '@/app/(admin)/admin/_components/dirty-form-context';
import type { OfferProvenanceView } from '@/lib/admin-catalogue/offer-view';

const MARKER = '2026-02-05T09:00:00.123456Z';
const IMPORTED: OfferProvenanceView = {
  provider: 'awin',
  externalId: '1001',
  appliedAt: '2026-02-01T09:00:00Z',
  sourceChangedAt: null,
};
const MOVED: OfferProvenanceView = { ...IMPORTED, sourceChangedAt: MARKER };
const CHANGED_NOTICE = 'This offer changed since you opened it.';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function Probe() {
  const { changed } = useOfferReview();
  useReportLoadedToken('tok-loaded');
  return <p data-testid="probe">{changed ? 'changed' : 'unchanged'}</p>;
}

function Dirty() {
  useReportFormDirty(true);
  return null;
}

function Reloading() {
  useReportFormBusy(true);
  return null;
}

type Extras = { dirty?: boolean; reloading?: boolean };
type PanelProps = Partial<React.ComponentProps<typeof OfferProvenancePanel>>;

function tree(
  provenance: OfferProvenanceView,
  props: PanelProps = {},
  extras: Extras = {}
): ReactNode {
  return (
    <DirtyFormProvider>
      <OfferReviewProvider>
        {extras.dirty ? <Dirty /> : null}
        {extras.reloading ? <Reloading /> : null}
        <Probe />
        <OfferProvenancePanel
          provenance={provenance}
          offerId="o1"
          offerTitle="Free coffee"
          status="DRAFT"
          reviewToken="tok-loaded"
          {...props}
        />
      </OfferReviewProvider>
    </DirtyFormProvider>
  );
}

const renderPanel = (
  provenance: OfferProvenanceView,
  props: PanelProps = {},
  extras: Extras = {}
) => render(tree(provenance, props, extras));

const trigger = () => screen.queryByRole('button', { name: 'Dismiss source change' });
const confirmButton = (dialog: HTMLElement) =>
  within(dialog).getByRole('button', { name: 'Dismiss change' });

async function confirmDismissal(user: ReturnType<typeof userEvent.setup>) {
  await user.click(trigger()!);
  await user.click(confirmButton(await screen.findByRole('dialog')));
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.dismissSourceChangeAction.mockResolvedValue({
    success: true,
    data: { id: 'o1', slug: 's', status: 'DRAFT', reviewToken: 'tok-loaded' },
  });
});

describe('OfferProvenancePanel', () => {
  it('shows where the offer came from, read only, with dates formatted for admins', () => {
    renderPanel(MOVED);

    const panel = screen.getByRole('region', { name: 'Provenance' });
    const terms = within(panel)
      .getAllByRole('term')
      .map((term) => term.textContent);
    expect(terms).toEqual(['Network', 'External ID', 'Last imported', 'Source changed']);
    const value = (label: string) => screen.getByText(label).nextElementSibling;
    expect(value('Network')).toHaveTextContent('Awin');
    expect(value('External ID')).toHaveTextContent('1001');
    expect(value('Last imported')).toHaveTextContent('1 Feb 2026');
    expect(value('Source changed')).toHaveTextContent('5 Feb 2026');
    expect(within(panel).queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('says the source has not changed when there is no marker, and offers no button', () => {
    renderPanel(IMPORTED);

    expect(screen.getByText('Source changed').nextElementSibling).toHaveTextContent('No');
    expect(screen.getByText('Source changed: No')).toBeInTheDocument();
    expect(trigger()).not.toBeInTheDocument();
  });

  it('shows a network it does not know by its own name as plain text', () => {
    renderPanel({ ...IMPORTED, provider: 'newnet' });

    expect(screen.getByText('Network').nextElementSibling).toHaveTextContent('newnet');
  });

  it('explains the marker in words, not colour', () => {
    renderPanel(MOVED);

    expect(
      screen.getByText(/The source of this offer has changed since it was imported/)
    ).toBeInTheDocument();
  });

  it.each(['DRAFT', 'PUBLISHED', 'EXPIRED'] as const)(
    'offers the button for a %s offer whose source changed',
    (status) => {
      renderPanel(MOVED, { status });
      expect(trigger()).toBeInTheDocument();
    }
  );

  it.each(['ARCHIVED', 'UNKNOWN'] as const)(
    'shows no button for a %s offer, and says why',
    (status) => {
      renderPanel(MOVED, { status });
      expect(trigger()).not.toBeInTheDocument();
      expect(screen.getByRole('region', { name: 'Provenance' })).toBeInTheDocument();
      expect(screen.getByText(/cannot be dismissed/)).toBeInTheDocument();
    }
  );
});

describe('Dismiss source change', () => {
  it('asks for confirmation first and calls nothing when cancelled', async () => {
    const user = setupUser();
    renderPanel(MOVED);

    await user.click(trigger()!);

    const dialog = await screen.findByRole('dialog', { name: 'Dismiss this source change?' });
    expect(within(dialog).getByText(/Free coffee/)).toBeInTheDocument();
    expect(mocks.dismissSourceChangeAction).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(mocks.dismissSourceChangeAction).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('names the trigger and the confirmation differently', async () => {
    const user = setupUser();
    renderPanel(MOVED);

    await user.click(trigger()!);

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Dismiss change' })).toBeInTheDocument();
    expect(
      within(dialog).queryByRole('button', { name: 'Dismiss source change' })
    ).not.toBeInTheDocument();
  });

  it.each(['{Enter}', ' '])('opens with the %j key from the keyboard', async (key) => {
    const user = setupUser();
    renderPanel(MOVED);

    trigger()!.focus();
    await user.keyboard(key);

    expect(
      await screen.findByRole('dialog', { name: 'Dismiss this source change?' })
    ).toBeVisible();
  });

  it('closes on Escape and returns focus to the trigger, calling nothing', async () => {
    const user = setupUser();
    renderPanel(MOVED);

    await user.click(trigger()!);
    await screen.findByRole('dialog');
    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(trigger()).toHaveFocus());
    expect(mocks.dismissSourceChangeAction).not.toHaveBeenCalled();
  });

  it('returns focus to the trigger after Cancel', async () => {
    const user = setupUser();
    renderPanel(MOVED);

    await user.click(trigger()!);
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' })
    );

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(trigger()).toHaveFocus());
  });

  it('dismisses with the review token and the marker the page loaded, announces it and refreshes', async () => {
    const user = setupUser();
    renderPanel(MOVED);

    await confirmDismissal(user);

    await waitFor(() =>
      expect(mocks.dismissSourceChangeAction).toHaveBeenCalledWith('o1', 'tok-loaded', MARKER)
    );
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        title: 'Source change dismissed',
        description: 'Free coffee',
      })
    );
    expect(mocks.refresh).toHaveBeenCalled();
    expect(screen.getByTestId('probe')).toHaveTextContent('unchanged');
  });

  it('runs once, keeps focus on the trigger and says what it is doing while it is pending', async () => {
    const pending = deferred<{ success: true; data: { id: string } }>();
    mocks.dismissSourceChangeAction.mockReturnValue(pending.promise);
    const user = setupUser();
    renderPanel(MOVED);

    await confirmDismissal(user);
    await waitFor(() => expect(trigger()).toHaveFocus());
    expect(trigger()).toHaveAttribute('aria-disabled', 'true');
    expect(trigger()).not.toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('Updating Free coffee');

    await user.dblClick(trigger()!);
    await user.click(trigger()!);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mocks.dismissSourceChangeAction).toHaveBeenCalledTimes(1);
    expect(trigger()).toHaveFocus();

    pending.resolve({ success: true, data: { id: 'o1' } });
    await waitFor(() => expect(screen.getByRole('status')).toBeEmptyDOMElement());
  });

  it('raises the lasting notice when somebody else changed the offer before the dismissal', async () => {
    mocks.dismissSourceChangeAction.mockResolvedValue({
      success: true,
      data: {
        id: 'o1',
        slug: 's',
        status: 'DRAFT',
        reviewToken: 'tok-new',
        previousReviewToken: 'tok-other',
      },
    });
    const user = setupUser();
    renderPanel(MOVED);

    await confirmDismissal(user);

    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('changed'));
  });

  it('shows a stale token or marker as the existing review-again message and raises the lasting notice', async () => {
    mocks.dismissSourceChangeAction.mockResolvedValue({
      success: false,
      error: `${CHANGED_NOTICE} Review it again.`,
      reason: 'offer_changed',
    });
    const user = setupUser();
    renderPanel(MOVED);

    await confirmDismissal(user);

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        variant: 'destructive',
        title: 'Could not dismiss the source change',
        description: `${CHANGED_NOTICE} Review it again.`,
      })
    );
    expect(screen.getByTestId('probe')).toHaveTextContent('changed');
    expect(mocks.refresh).toHaveBeenCalled();
    // The button is still there (the marker is still set), and so is the focus.
    await waitFor(() => expect(trigger()).toHaveFocus());
  });

  it('shows another refusal as a destructive toast without the lasting notice', async () => {
    mocks.dismissSourceChangeAction.mockResolvedValue({
      success: false,
      error: 'You do not have permission to do that',
    });
    const user = setupUser();
    renderPanel(MOVED);

    await confirmDismissal(user);

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        variant: 'destructive',
        title: 'Could not dismiss the source change',
        description: 'You do not have permission to do that',
      })
    );
    expect(screen.getByTestId('probe')).toHaveTextContent('unchanged');
  });

  it('turns a server action that throws into a curated failure', async () => {
    mocks.dismissSourceChangeAction.mockRejectedValue(new Error('SECRET network drop'));
    const user = setupUser();
    renderPanel(MOVED);

    await confirmDismissal(user);

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({
          variant: 'destructive',
          description: 'Something went wrong. Please try again.',
        })
      )
    );
    expect(JSON.stringify(mocks.toast.mock.calls)).not.toContain('SECRET');
  });
});

describe('Dismiss source change waits like the other status buttons', () => {
  it('waits while the form beside it holds unsaved edits, opening nothing and calling nothing', async () => {
    const user = setupUser();
    renderPanel(MOVED, {}, { dirty: true });

    expect(trigger()).toHaveAttribute('aria-disabled', 'true');
    await user.click(trigger()!);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mocks.dismissSourceChangeAction).not.toHaveBeenCalled();
  });

  it('waits while a save is reloading into the page, opening nothing and calling nothing', async () => {
    const user = setupUser();
    renderPanel(MOVED, {}, { reloading: true });

    expect(trigger()).toHaveAttribute('aria-disabled', 'true');
    await user.click(trigger()!);
    await user.keyboard('{Enter}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mocks.dismissSourceChangeAction).not.toHaveBeenCalled();
  });

  it('leaves the reason to the lifecycle group: its own live region says nothing about it', () => {
    renderPanel(MOVED, {}, { dirty: true, reloading: true });

    // One live region per component: the reason ("Save or discard your changes first") is announced once, by
    // the lifecycle buttons, not repeated here.
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
    expect(screen.queryByText(/Save or discard/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Loading the saved version/)).not.toBeInTheDocument();
  });
});

describe('focus after the button goes away', () => {
  it('moves to the replacement text once the dismissal lands and the marker is gone', async () => {
    const user = setupUser();
    const view = renderPanel(MOVED);

    await confirmDismissal(user);
    await waitFor(() => expect(mocks.dismissSourceChangeAction).toHaveBeenCalledTimes(1));
    // router.refresh() brings the offer back without a marker (and the same token: the offer did not change).
    view.rerender(tree(IMPORTED));

    const replacement = await screen.findByText('Source change dismissed.');
    await waitFor(() => expect(replacement).toHaveFocus());
    expect(trigger()).not.toBeInTheDocument();
  });

  it('does not pull focus anywhere when the admin was elsewhere on the page', async () => {
    const user = setupUser();
    const page = (provenance: OfferProvenanceView) => (
      <div>
        <button type="button">Elsewhere</button>
        {tree(provenance)}
      </div>
    );
    const view = render(page(MOVED));

    await confirmDismissal(user);
    await waitFor(() => expect(mocks.dismissSourceChangeAction).toHaveBeenCalledTimes(1));
    screen.getByRole('button', { name: 'Elsewhere' }).focus();
    view.rerender(page(IMPORTED));

    await screen.findByText('Source change dismissed.');
    expect(screen.getByRole('button', { name: 'Elsewhere' })).toHaveFocus();
  });

  it('does nothing for a re-render that changes nothing', () => {
    const view = renderPanel(MOVED);
    trigger()!.focus();

    view.rerender(tree(MOVED));

    expect(trigger()).toHaveFocus();
  });
});
