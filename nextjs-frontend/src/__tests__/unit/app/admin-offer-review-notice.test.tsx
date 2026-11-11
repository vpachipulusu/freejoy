/**
 * The edit page's buttons and the review token: a lasting notice when the offer changed under the editor, the
 * token of the editor's own last write, the sequence verify then publish, and focus after a button disappears.
 */
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { findIdleControl } from '@/__tests__/unit/helpers/idle';

const mocks = vi.hoisted(() => ({
  offerLifecycleAction: vi.fn(),
  toast: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock('@/app/actions/admin/offer-actions', () => ({
  offerLifecycleAction: mocks.offerLifecycleAction,
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: mocks.refresh, push: vi.fn() }),
}));

import { DirtyFormProvider } from '@/app/(admin)/admin/_components/dirty-form-context';
import { OfferLifecycleButtons } from '@/app/(admin)/admin/offers/_components/offer-lifecycle-buttons';
import { OfferReviewProvider } from '@/app/(admin)/admin/offers/_components/offer-review-context';

const CHANGED_ERROR = 'This offer changed since you opened it. Review it again.';
const ALERT =
  'This offer changed since you opened it. This page now shows the latest version: review it again.';
const HEX = (digit: string) => digit.repeat(64);

type Status = React.ComponentProps<typeof OfferLifecycleButtons>['status'];

function page(token: string, status: Status = 'DRAFT'): ReactNode {
  return (
    <DirtyFormProvider>
      <OfferReviewProvider>
        <OfferLifecycleButtons
          commercialBasis="EDITORIAL"
          confirmBasisToPublish={false}
          offerId="o1"
          offerTitle="Free coffee"
          status={status}
          reviewToken={token}
        />
      </OfferReviewProvider>
    </DirtyFormProvider>
  );
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((done) => (resolve = done));
  return { promise, resolve };
}

/** A successful write: the token of the state after it, and the token of the state it replaced. */
const written = (reviewToken: string, status = 'DRAFT', previousReviewToken?: string) => ({
  success: true as const,
  data: { id: 'o1', slug: 'free-coffee', status, reviewToken, previousReviewToken },
});

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('the notice that the offer changed', () => {
  it('stays after the 5 second toast is gone and clears after a later success', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = setupUser({ advanceTimers: vi.advanceTimersByTime });
    mocks.offerLifecycleAction.mockResolvedValueOnce({
      success: false,
      error: CHANGED_ERROR,
      reason: 'offer_changed',
    });
    const { rerender } = render(page(HEX('a')));

    await user.click(screen.getByRole('button', { name: 'Publish' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(ALERT);
    act(() => {
      vi.advanceTimersByTime(6000);
    });
    expect(screen.getByRole('alert')).toHaveTextContent(ALERT);

    // The refresh lands with the new version; the notice is still there.
    rerender(page(HEX('b')));
    expect(screen.getByRole('alert')).toHaveTextContent(ALERT);

    mocks.offerLifecycleAction.mockResolvedValueOnce(written(HEX('c'), 'DRAFT', HEX('b')));
    await user.click(screen.getByRole('button', { name: 'Mark verified' }));
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalledTimes(2));
    rerender(page(HEX('c')));

    // The end state, not a moment on the way to it: the reload with the editor's own token re-raises nothing.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows no notice after an ordinary success whose refresh lands with the token it returned', async () => {
    const user = setupUser();
    mocks.offerLifecycleAction.mockResolvedValueOnce(written(HEX('b'), 'DRAFT', HEX('a')));
    const { rerender } = render(page(HEX('a')));

    await user.click(screen.getByRole('button', { name: 'Mark verified' }));
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());
    rerender(page(HEX('b')));

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows the notice when the refresh brings a token other than the one the editor action returned', async () => {
    const user = setupUser();
    mocks.offerLifecycleAction.mockResolvedValueOnce(written(HEX('b'), 'DRAFT', HEX('a')));
    const { rerender } = render(page(HEX('a')));

    await user.click(screen.getByRole('button', { name: 'Mark verified' }));
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());
    rerender(page(HEX('c')));

    expect(await screen.findByRole('alert')).toHaveTextContent(ALERT);
  });

  it('does not mistake the editor own refresh landing before the action result for a change', async () => {
    const user = setupUser();
    const pending = deferred<ReturnType<typeof written>>();
    mocks.offerLifecycleAction.mockReturnValueOnce(pending.promise);
    mocks.offerLifecycleAction.mockResolvedValueOnce(written(HEX('d')));
    const { rerender } = render(page(HEX('a')));
    // A first action of the editor's own, so a token of theirs is on record.
    await user.click(screen.getByRole('button', { name: 'Mark verified' }));
    pending.resolve(written(HEX('b'), 'DRAFT', HEX('a')));
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalledTimes(1));
    rerender(page(HEX('b')));

    // The second action: the refreshed page arrives before the action's own result does.
    const second = deferred<ReturnType<typeof written>>();
    mocks.offerLifecycleAction.mockReset();
    mocks.offerLifecycleAction.mockReturnValueOnce(second.promise);
    await user.click(screen.getByRole('button', { name: 'Mark verified' }));
    rerender(page(HEX('c')));
    await act(async () => second.resolve(written(HEX('c'), 'DRAFT', HEX('b'))));

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows the notice when a different version lands while the editor action is still in flight', async () => {
    const user = setupUser();
    const pending = deferred<ReturnType<typeof written>>();
    mocks.offerLifecycleAction.mockReturnValueOnce(pending.promise);
    const { rerender } = render(page(HEX('a')));

    await user.click(screen.getByRole('button', { name: 'Mark verified' }));
    rerender(page(HEX('z')));
    await act(async () => pending.resolve(written(HEX('b'), 'DRAFT', HEX('a'))));

    expect(await screen.findByRole('alert')).toHaveTextContent(ALERT);
  });

  it.each(['Unpublish', 'Expire', 'Archive'])(
    'alerts when the state %s replaced was not the one the editor loaded (a foreign change came first)',
    async (label) => {
      const user = setupUser();
      mocks.offerLifecycleAction.mockResolvedValueOnce(written(HEX('b'), 'EXPIRED', HEX('x')));
      render(page(HEX('a'), 'PUBLISHED'));

      await user.click(screen.getByRole('button', { name: label }));
      if (label === 'Archive') {
        await user.click(
          within(await screen.findByRole('dialog')).getByRole('button', { name: 'Archive offer' })
        );
      }

      expect(await screen.findByRole('alert')).toHaveTextContent(ALERT);
    }
  );

  it('does not alert when the state a step replaced is the one the editor loaded', async () => {
    const user = setupUser();
    mocks.offerLifecycleAction.mockResolvedValueOnce(written(HEX('b'), 'EXPIRED', HEX('a')));
    render(page(HEX('a'), 'PUBLISHED'));

    await user.click(screen.getByRole('button', { name: 'Expire' }));
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('alerts when a refused step is followed by a reload that brings a different token', async () => {
    const user = setupUser();
    mocks.offerLifecycleAction.mockResolvedValueOnce({
      success: false,
      error: 'That change is not allowed for an offer in its current status.',
    });
    const { rerender } = render(page(HEX('a'), 'PUBLISHED'));

    await user.click(screen.getByRole('button', { name: 'Expire' }));
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    rerender(page(HEX('n'), 'PUBLISHED'));

    expect(screen.getByRole('alert')).toHaveTextContent(ALERT);
  });

  it('alerts on a reload with a new token even before the editor has written anything', () => {
    const { rerender } = render(page(HEX('a')));

    rerender(page(HEX('b')));

    expect(screen.getByRole('alert')).toHaveTextContent(ALERT);
  });
});

describe('verify, then publish', () => {
  it('sends the token of the version the first success returned', async () => {
    const user = setupUser();
    mocks.offerLifecycleAction.mockResolvedValueOnce(written(HEX('b'), 'DRAFT', HEX('a')));
    mocks.offerLifecycleAction.mockResolvedValueOnce(written(HEX('c'), 'PUBLISHED', HEX('b')));
    const { rerender } = render(page(HEX('a')));

    await user.click(screen.getByRole('button', { name: 'Mark verified' }));
    await waitFor(() =>
      expect(mocks.offerLifecycleAction).toHaveBeenLastCalledWith('o1', 'verify', HEX('a'))
    );
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());
    rerender(page(HEX('b')));
    await user.click(await findIdleControl('button', 'Publish'));

    await waitFor(() =>
      expect(mocks.offerLifecycleAction).toHaveBeenLastCalledWith('o1', 'publish', HEX('b'))
    );
  });
});

describe('focus after the pressed button goes away', () => {
  it('moves into the group when Publish disappears because the offer is now published', async () => {
    const user = setupUser();
    mocks.offerLifecycleAction.mockResolvedValueOnce(written(HEX('b'), 'PUBLISHED', HEX('a')));
    const { rerender } = render(page(HEX('a'), 'DRAFT'));

    await user.click(screen.getByRole('button', { name: 'Publish' }));
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());
    rerender(page(HEX('b'), 'PUBLISHED'));

    const group = screen.getByRole('group', { name: 'Offer status actions' });
    expect(screen.queryByRole('button', { name: 'Publish' })).not.toBeInTheDocument();
    await waitFor(() => expect(group).toContainElement(document.activeElement as HTMLElement));
    expect(within(group).getAllByRole('button')[0]).toHaveFocus();
  });

  it('moves to the status line when the offer has no step left (archived)', async () => {
    const user = setupUser();
    mocks.offerLifecycleAction.mockResolvedValueOnce(written(HEX('b'), 'ARCHIVED', HEX('a')));
    const { rerender } = render(page(HEX('a'), 'DRAFT'));

    await user.click(screen.getByRole('button', { name: 'Archive' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Archive offer' })
    );
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());
    rerender(page(HEX('b'), 'ARCHIVED'));

    await waitFor(() => expect(screen.getByText(/archived and cannot be changed/)).toHaveFocus());
  });
});
