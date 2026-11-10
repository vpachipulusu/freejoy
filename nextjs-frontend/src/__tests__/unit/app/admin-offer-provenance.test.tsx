/**
 * The offer page's Provenance panel and its "Dismiss source change" button: what the panel shows, when the
 * button exists, the confirmation, and how an outcome is announced (the same wiring as Mark verified: a toast,
 * the review state, then router.refresh()).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

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
  useReportFormDirty,
} from '@/app/(admin)/admin/_components/dirty-form-context';
import type { OfferProvenanceView } from '@/lib/admin-catalogue/offer-view';

const IMPORTED: OfferProvenanceView = {
  provider: 'awin',
  externalId: '1001',
  appliedAt: '2026-02-01T09:00:00Z',
  sourceChangedAt: null,
};
const MOVED: OfferProvenanceView = { ...IMPORTED, sourceChangedAt: '2026-02-05T09:00:00Z' };
const CHANGED_NOTICE = 'This offer changed since you opened it.';

function Probe() {
  const { changed } = useOfferReview();
  useReportLoadedToken('tok-loaded');
  return <p data-testid="probe">{changed ? 'changed' : 'unchanged'}</p>;
}

function Dirty() {
  useReportFormDirty(true);
  return null;
}

function renderPanel(
  provenance: OfferProvenanceView,
  props: Partial<React.ComponentProps<typeof OfferProvenancePanel>> = {},
  { dirty = false }: { dirty?: boolean } = {}
) {
  return render(
    <DirtyFormProvider>
      <OfferReviewProvider>
        {dirty ? <Dirty /> : null}
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

const dismissButton = () => screen.queryByRole('button', { name: 'Dismiss source change' });

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
    expect(dismissButton()).not.toBeInTheDocument();
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
      expect(dismissButton()).toBeInTheDocument();
    }
  );

  it.each(['ARCHIVED', 'UNKNOWN'] as const)(
    'shows no button for a %s offer: nothing can be done to it',
    (status) => {
      renderPanel(MOVED, { status });
      expect(dismissButton()).not.toBeInTheDocument();
      expect(screen.getByRole('region', { name: 'Provenance' })).toBeInTheDocument();
    }
  );
});

describe('Dismiss source change', () => {
  it('asks for confirmation first and calls nothing when cancelled', async () => {
    const user = userEvent.setup();
    renderPanel(MOVED);

    await user.click(dismissButton()!);

    const dialog = await screen.findByRole('dialog', { name: 'Dismiss this source change?' });
    expect(within(dialog).getByText(/Free coffee/)).toBeInTheDocument();
    expect(mocks.dismissSourceChangeAction).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(mocks.dismissSourceChangeAction).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('dismisses with the review token the page loaded, announces it and refreshes', async () => {
    const user = userEvent.setup();
    renderPanel(MOVED);

    await user.click(dismissButton()!);
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Dismiss source change' }));

    await waitFor(() =>
      expect(mocks.dismissSourceChangeAction).toHaveBeenCalledWith('o1', 'tok-loaded')
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
    const user = userEvent.setup();
    renderPanel(MOVED);

    await user.click(dismissButton()!);
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', {
        name: 'Dismiss source change',
      })
    );

    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('changed'));
  });

  it('shows a stale token as the existing review-again message and raises the lasting notice', async () => {
    mocks.dismissSourceChangeAction.mockResolvedValue({
      success: false,
      error: `${CHANGED_NOTICE} Review it again.`,
      reason: 'offer_changed',
    });
    const user = userEvent.setup();
    renderPanel(MOVED);

    await user.click(dismissButton()!);
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', {
        name: 'Dismiss source change',
      })
    );

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        variant: 'destructive',
        title: 'Could not dismiss the source change',
        description: `${CHANGED_NOTICE} Review it again.`,
      })
    );
    expect(screen.getByTestId('probe')).toHaveTextContent('changed');
    expect(mocks.refresh).toHaveBeenCalled();
  });

  it('shows another refusal as a destructive toast without the lasting notice', async () => {
    mocks.dismissSourceChangeAction.mockResolvedValue({
      success: false,
      error: 'You do not have permission to do that',
    });
    const user = userEvent.setup();
    renderPanel(MOVED);

    await user.click(dismissButton()!);
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', {
        name: 'Dismiss source change',
      })
    );

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
    const user = userEvent.setup();
    renderPanel(MOVED);

    await user.click(dismissButton()!);
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', {
        name: 'Dismiss source change',
      })
    );

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

  it('waits while the form beside it holds unsaved edits, and says why', async () => {
    const user = userEvent.setup();
    renderPanel(MOVED, {}, { dirty: true });

    const button = dismissButton()!;
    expect(button).toHaveAttribute('aria-disabled', 'true');
    await user.click(button);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mocks.dismissSourceChangeAction).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent('Save or discard your changes first');
  });
});
