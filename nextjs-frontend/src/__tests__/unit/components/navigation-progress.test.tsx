import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import {
  NAVIGATION_SHOW_DELAY_MS,
  NAVIGATION_TIMEOUT_MS,
  NavigationProgress,
} from '@/components/site/navigation-progress';
import { announceFormNavigation, announceNavigation } from '@/components/site/navigation-events';

const route = vi.hoisted(() => ({ pathname: '/', search: '' }));

vi.mock('next/navigation', () => ({
  usePathname: () => route.pathname,
  useSearchParams: () => new URLSearchParams(route.search),
}));

const INDICATOR = 'navigation-progress';
const SPINNER = 'navigation-progress-spinner';
const STATUS = 'navigation-progress-status';

/** Renders the indicator beside a link the test clicks; the click's default action is cancelled (jsdom cannot navigate). */
function renderWithLink(attrs: Record<string, string> = {}, href = '/about') {
  const ui = () => (
    <>
      <NavigationProgress />
      <a href={href} data-testid="link" onClick={(event) => event.preventDefault()} {...attrs}>
        link <span data-testid="inner">inner</span>
      </a>
    </>
  );
  const view = render(ui());
  return { ...view, rerender: () => view.rerender(ui()), link: screen.getByTestId('link') };
}

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

/** Whether the bar is on screen right now (the show delay may not have passed yet). */
function isVisibleNow() {
  return screen.queryByTestId(INDICATOR) !== null;
}

/** Lets the show delay pass, then says whether the indicator is on screen. */
function isShown() {
  advance(NAVIGATION_SHOW_DELAY_MS);
  return isVisibleNow();
}

/** Moves the "router" to another URL and renders again, as Next does when a navigation commits. */
function navigateTo(rerender: () => void, pathname: string, search = '') {
  route.pathname = pathname;
  route.search = search;
  rerender();
}

beforeEach(() => {
  vi.useFakeTimers();
  route.pathname = '/';
  route.search = '';
  window.history.pushState({}, '', '/');
});

afterEach(() => {
  vi.useRealTimers();
});

describe('NavigationProgress', () => {
  describe('Given the first page load', () => {
    it('should not show the indicator', () => {
      render(<NavigationProgress />);

      expect(isShown()).toBe(false);
      expect(screen.queryByTestId(SPINNER)).toBeNull();
    });

    it('should keep one empty status region ready for screen readers', () => {
      render(<NavigationProgress />);

      const status = screen.getByRole('status');
      expect(status).toHaveAttribute('data-testid', STATUS);
      expect(status).toHaveClass('sr-only');
      expect(status).toBeEmptyDOMElement();
    });
  });

  describe('Given a click on a link', () => {
    it('should start for an internal link to another page', () => {
      const { link } = renderWithLink();

      fireEvent.click(link);

      expect(isShown()).toBe(true);
    });

    it('should say "Loading" in the one status region and draw a decorative spinner', () => {
      const { link } = renderWithLink();

      fireEvent.click(link);
      expect(isShown()).toBe(true);

      expect(screen.getAllByRole('status')).toHaveLength(1);
      expect(screen.getByRole('status')).toHaveTextContent('Loading');
      expect(screen.getByTestId(SPINNER).querySelector('[aria-hidden="true"]')).not.toBeNull();
    });

    it('should wait for the show delay, so a quick navigation never flashes', () => {
      const { link } = renderWithLink();

      fireEvent.click(link);
      advance(NAVIGATION_SHOW_DELAY_MS - 1);
      expect(isVisibleNow()).toBe(false);

      advance(1);
      expect(isVisibleNow()).toBe(true);
    });

    it('should never show when the page arrives before the show delay', () => {
      const { link, rerender } = renderWithLink();
      fireEvent.click(link);
      advance(NAVIGATION_SHOW_DELAY_MS - 1);

      navigateTo(rerender, '/about');

      expect(isShown()).toBe(false);
    });

    it('should start when the click lands on an element inside the link', () => {
      renderWithLink();

      fireEvent.click(screen.getByTestId('inner'));

      expect(isShown()).toBe(true);
    });

    it('should start for a link that keeps the query string of another page', () => {
      const { link } = renderWithLink({}, '/freebies?page=2');

      fireEvent.click(link);

      expect(isShown()).toBe(true);
    });

    it.each([
      ['an external link', { href: 'https://example.com/about' }],
      ['a mailto link', { href: 'mailto:hello@example.com' }],
      ['a hash link', { href: '#main-content' }],
      ['a hash link on this page', { href: '/#faq' }],
      ['a link to the current page', { href: '/' }],
      ['a link that opens in a new tab', { target: '_blank' }],
      ['a download link', { download: 'file.json' }],
      ['an API path', { href: '/api/health' }],
    ])('should ignore %s', (_name, attrs) => {
      const { link } = renderWithLink(attrs);

      fireEvent.click(link);

      expect(isShown()).toBe(false);
    });

    it.each([
      ['ctrl', { ctrlKey: true }],
      ['meta', { metaKey: true }],
      ['shift', { shiftKey: true }],
      ['alt', { altKey: true }],
      ['the middle button', { button: 1 }],
    ])('should ignore a click with %s', (_name, init) => {
      const { link } = renderWithLink();

      fireEvent.click(link, init);

      expect(isShown()).toBe(false);
    });

    it('should ignore a click that a handler already cancelled', () => {
      const { link } = renderWithLink();
      window.addEventListener('click', (event) => event.preventDefault(), {
        capture: true,
        once: true,
      });

      fireEvent.click(link);

      expect(isShown()).toBe(false);
    });

    it('should ignore a click that is not on a link', () => {
      renderWithLink();

      fireEvent.click(document.body);

      expect(isShown()).toBe(false);
    });
  });

  describe('Given a navigation in progress', () => {
    it('should stop when the path changes', () => {
      const { link, rerender } = renderWithLink();
      fireEvent.click(link);
      expect(isShown()).toBe(true);

      navigateTo(rerender, '/about');

      expect(isVisibleNow()).toBe(false);
      expect(screen.getByRole('status')).toBeEmptyDOMElement();
    });

    it('should stop when only the query string changes', () => {
      const { link, rerender } = renderWithLink({}, '/?q=cat');
      fireEvent.click(link);
      expect(isShown()).toBe(true);

      navigateTo(rerender, '/', 'q=cat');

      expect(isVisibleNow()).toBe(false);
    });

    it('should be able to start again after it stopped', () => {
      const { link, rerender } = renderWithLink();
      fireEvent.click(link);
      navigateTo(rerender, '/about');

      fireEvent.click(link);

      expect(isShown()).toBe(true);
    });

    it('should stop after the safety timeout when the URL never changes', () => {
      const { link } = renderWithLink();
      fireEvent.click(link);

      advance(NAVIGATION_TIMEOUT_MS - 1);
      expect(isVisibleNow()).toBe(true);

      advance(1);
      expect(isVisibleNow()).toBe(false);
    });

    it('should restart the timeout when another link is clicked', () => {
      const { link } = renderWithLink();
      fireEvent.click(link);
      advance(NAVIGATION_TIMEOUT_MS - 1000);

      fireEvent.click(link);
      advance(NAVIGATION_TIMEOUT_MS - 1000);

      expect(isVisibleNow()).toBe(true);
    });

    it('should not stop for an unrelated error or promise rejection', () => {
      const { link } = renderWithLink();
      fireEvent.click(link);
      expect(isShown()).toBe(true);

      act(() => {
        window.dispatchEvent(new Event('error'));
        window.dispatchEvent(new Event('unhandledrejection'));
      });

      expect(isVisibleNow()).toBe(true);
    });

    it('should stop when the page is restored from the back-forward cache', () => {
      const { link } = renderWithLink();
      fireEvent.click(link);
      expect(isShown()).toBe(true);

      act(() => {
        window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
      });

      expect(isVisibleNow()).toBe(false);
    });

    it('should keep going on a normal pageshow', () => {
      const { link } = renderWithLink();
      fireEvent.click(link);
      expect(isShown()).toBe(true);

      act(() => {
        window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: false }));
      });

      expect(isVisibleNow()).toBe(true);
    });
  });

  describe('Given the indicator is showing', () => {
    it('should not block clicks and should not move the layout', () => {
      const { link } = renderWithLink();
      fireEvent.click(link);
      expect(isShown()).toBe(true);

      for (const element of [screen.getByTestId(INDICATOR), screen.getByTestId(SPINNER)]) {
        expect(element).toHaveClass('pointer-events-none', 'fixed');
      }
      expect(screen.getByTestId(INDICATOR)).toHaveAttribute('aria-hidden', 'true');
    });

    it('should keep the spinner under dialogs (z-40, a dialog is z-50)', () => {
      const { link } = renderWithLink();
      fireEvent.click(link);
      expect(isShown()).toBe(true);

      expect(screen.getByTestId(SPINNER)).toHaveClass('z-40');
    });
  });

  describe('Given a form submit', () => {
    function renderWithForm(formAttrs: Record<string, string>, prevent = false) {
      render(
        <>
          <NavigationProgress />
          <form
            data-testid="form"
            {...formAttrs}
            onSubmit={prevent ? (event) => event.preventDefault() : undefined}
          >
            <input name="q" defaultValue="cat" />
          </form>
        </>
      );
      return screen.getByTestId('form');
    }

    function submit(form: HTMLElement) {
      fireEvent.submit(form);
      advance(0);
    }

    it('should start for a GET form that goes to another URL', () => {
      submit(renderWithForm({ action: '/admin/users', method: 'get' }));

      expect(isShown()).toBe(true);
    });

    it('should treat a form without a method as GET', () => {
      submit(renderWithForm({ action: '/freebies' }));

      expect(isShown()).toBe(true);
    });

    it('should ignore a POST form', () => {
      submit(renderWithForm({ action: '/admin/users', method: 'post' }));

      expect(isShown()).toBe(false);
    });

    it('should ignore a form whose result is the current URL', () => {
      window.history.pushState({}, '', '/admin/users?q=cat');
      submit(renderWithForm({ action: '/admin/users', method: 'get' }));

      expect(isShown()).toBe(false);
    });

    it('should ignore a form that opens in a new tab', () => {
      submit(renderWithForm({ action: '/admin/users', target: '_blank' }));

      expect(isShown()).toBe(false);
    });

    it('should ignore a form that goes to another site', () => {
      submit(renderWithForm({ action: 'https://example.com/search' }));

      expect(isShown()).toBe(false);
    });

    it('should ignore a form that script handles (preventDefault)', () => {
      submit(renderWithForm({ action: '/admin/users', method: 'get' }, true));

      expect(isShown()).toBe(false);
    });

    it('should not read the form fields until the submit has gone through', () => {
      const form = renderWithForm({ action: '/admin/users', method: 'get' });
      const RealFormData = FormData;
      let built = 0;
      vi.stubGlobal(
        'FormData',
        class extends RealFormData {
          constructor(source?: HTMLFormElement) {
            super(source);
            built += 1;
          }
        }
      );

      fireEvent.submit(form);
      expect(built).toBe(0);

      advance(0);
      expect(built).toBe(1);
      vi.stubGlobal('FormData', RealFormData);
    });

    it('should honour the formmethod of the submit button', () => {
      render(
        <>
          <NavigationProgress />
          <form data-testid="form" action="/freebies" method="get">
            <input name="q" defaultValue="cat" />
            <button type="submit" formMethod="post" data-testid="post-button" />
          </form>
        </>
      );
      const submitter = screen.getByTestId('post-button');

      fireEvent(
        screen.getByTestId('form'),
        new SubmitEvent('submit', { bubbles: true, cancelable: true, submitter })
      );
      advance(0);

      expect(isShown()).toBe(false);
    });

    it('should honour the formaction and formtarget of the submit button', () => {
      render(
        <>
          <NavigationProgress />
          <form data-testid="form" action="/freebies" method="get">
            <button type="submit" formAction="/category" data-testid="go" />
            <button type="submit" formTarget="_blank" data-testid="new-tab" />
          </form>
        </>
      );

      fireEvent(
        screen.getByTestId('form'),
        new SubmitEvent('submit', {
          bubbles: true,
          cancelable: true,
          submitter: screen.getByTestId('new-tab'),
        })
      );
      advance(0);
      expect(isShown()).toBe(false);

      fireEvent(
        screen.getByTestId('form'),
        new SubmitEvent('submit', {
          bubbles: true,
          cancelable: true,
          submitter: screen.getByTestId('go'),
        })
      );
      advance(0);
      expect(isShown()).toBe(true);
    });

    it('should leave file fields out of the query string it compares', () => {
      render(
        <>
          <NavigationProgress />
          <form data-testid="form" action="/" method="get">
            <input type="file" name="upload" />
          </form>
        </>
      );

      submit(screen.getByTestId('form'));

      expect(isShown()).toBe(false);
    });

    it('should ignore a submit that is not from a form', () => {
      render(<NavigationProgress />);

      fireEvent.submit(document.body);
      advance(0);

      expect(isShown()).toBe(false);
    });

    it('should forget a submit that is still waiting when the component unmounts', () => {
      const { unmount } = render(<NavigationProgress />);
      const form = document.createElement('form');
      form.action = '/admin/users';
      document.body.append(form);

      fireEvent.submit(form);
      expect(vi.getTimerCount()).toBe(1);
      unmount();

      expect(vi.getTimerCount()).toBe(0);
      form.remove();
    });
  });

  describe('Given a next/form submit, which cancels the native event and pushes itself', () => {
    function renderNextForm() {
      render(
        <>
          <NavigationProgress />
          <form
            data-testid="form"
            action="/admin/audit-logs"
            onSubmit={(event) => {
              announceFormNavigation(event);
              event.preventDefault();
            }}
          >
            <input name="action" defaultValue="login" />
          </form>
        </>
      );
      return screen.getByTestId('form');
    }

    it('should start through announceFormNavigation although the submit was cancelled', () => {
      fireEvent.submit(renderNextForm());
      advance(0);

      expect(isShown()).toBe(true);
    });

    it('should announce the action with the fields as its query string', () => {
      const heard: string[] = [];
      window.addEventListener('freejoy:navigation-start', (event) =>
        heard.push((event as CustomEvent<{ href: string }>).detail.href)
      );

      fireEvent.submit(renderNextForm());

      expect(heard).toEqual(['http://localhost:3000/admin/audit-logs?action=login']);
    });

    it('should announce nothing when the submit leads to the page that is open', () => {
      window.history.pushState({}, '', '/admin/audit-logs?action=login');

      fireEvent.submit(renderNextForm());
      advance(0);

      expect(isShown()).toBe(false);
    });
  });

  describe('Given back or forward', () => {
    it('should start when the address moves to another page', () => {
      render(<NavigationProgress />);
      window.history.pushState({}, '', '/about');

      act(() => {
        window.dispatchEvent(new PopStateEvent('popstate'));
      });

      expect(isShown()).toBe(true);
    });

    it('should ignore a hash-only change', () => {
      render(<NavigationProgress />);
      window.history.pushState({}, '', '/#faq');

      act(() => {
        window.dispatchEvent(new PopStateEvent('popstate'));
      });

      expect(isShown()).toBe(false);
    });

    it('should compare with the page that is shown, not the first one', () => {
      const { rerender } = render(<NavigationProgress />);
      navigateTo(() => rerender(<NavigationProgress />), '/about');
      window.history.pushState({}, '', '/about');

      act(() => {
        window.dispatchEvent(new PopStateEvent('popstate'));
      });

      expect(isShown()).toBe(false);
    });

    it('should treat an encoded space in the address bar as the same query as useSearchParams', () => {
      route.search = 'q=a+b';
      render(<NavigationProgress />);
      window.history.pushState({}, '', '/?q=a%20b#x');

      act(() => {
        window.dispatchEvent(new PopStateEvent('popstate'));
      });

      expect(isShown()).toBe(false);
    });
  });

  describe('Given router.push through announceNavigation', () => {
    it('should start without an address', () => {
      render(<NavigationProgress />);

      act(() => announceNavigation());

      expect(isShown()).toBe(true);
    });

    it('should start for an address that is another page', () => {
      render(<NavigationProgress />);

      act(() => announceNavigation('/dashboard'));

      expect(isShown()).toBe(true);
    });

    it.each(['/', '/#top', 'https://example.com/', 'http://['])('should ignore %s', (href) => {
      render(<NavigationProgress />);

      act(() => announceNavigation(href));

      expect(isShown()).toBe(false);
    });

    it('should do nothing on the server', () => {
      const original = globalThis.window;
      vi.stubGlobal('window', undefined);
      try {
        expect(() => announceNavigation('/x')).not.toThrow();
        expect(() => announceFormNavigation({ nativeEvent: new Event('submit') })).not.toThrow();
      } finally {
        vi.stubGlobal('window', original);
      }
    });
  });

  describe('Given the component unmounts', () => {
    it('should remove every listener it added, with the same capture flag', () => {
      const docAdd = vi.spyOn(document, 'addEventListener');
      const docRemove = vi.spyOn(document, 'removeEventListener');
      const winAdd = vi.spyOn(window, 'addEventListener');
      const winRemove = vi.spyOn(window, 'removeEventListener');
      const { unmount } = render(<NavigationProgress />);

      const ours = ['click', 'submit', 'popstate', 'pageshow', 'freejoy:navigation-start'];
      const captureOf = (options: unknown) =>
        typeof options === 'boolean'
          ? options
          : Boolean((options as { capture?: boolean } | undefined)?.capture);
      const added = [...docAdd.mock.calls, ...winAdd.mock.calls]
        .filter(([type]) => ours.includes(type))
        .map(([type, handler, options]) => [type, handler, captureOf(options)]);
      unmount();
      const removed = [...docRemove.mock.calls, ...winRemove.mock.calls].map(
        ([type, handler, options]) => [type, handler, captureOf(options)]
      );

      expect(added).toHaveLength(ours.length);
      for (const entry of added) expect(removed).toContainEqual(entry);
      vi.restoreAllMocks();
    });

    it('should stop listening', () => {
      const { link, unmount } = renderWithLink();
      unmount();
      link.addEventListener('click', (event) => event.preventDefault());
      document.body.append(link);

      fireEvent.click(link);

      expect(isShown()).toBe(false);
      link.remove();
    });
  });

  describe('Given React StrictMode (effects run twice in development)', () => {
    it('should show once for one click and clear on one route change', () => {
      const ui = () => (
        <StrictMode>
          <NavigationProgress />
          <a href="/about" data-testid="link" onClick={(event) => event.preventDefault()}>
            link
          </a>
        </StrictMode>
      );
      const { rerender } = render(ui());

      fireEvent.click(screen.getByTestId('link'));
      advance(NAVIGATION_SHOW_DELAY_MS);
      expect(screen.getAllByTestId(INDICATOR)).toHaveLength(1);

      navigateTo(() => rerender(ui()), '/about');

      expect(screen.queryAllByTestId(INDICATOR)).toHaveLength(0);
    });
  });
});
