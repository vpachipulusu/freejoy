import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { NavigationProgress, NAVIGATION_TIMEOUT_MS } from '@/components/site/navigation-progress';
import { announceNavigation } from '@/components/site/navigation-events';

const route = vi.hoisted(() => ({ pathname: '/', search: '' }));

vi.mock('next/navigation', () => ({
  usePathname: () => route.pathname,
  useSearchParams: () => new URLSearchParams(route.search),
}));

const INDICATOR = 'navigation-progress';

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

function isShown() {
  return screen.queryByTestId(INDICATOR) !== null;
}

/** Moves the "router" to another URL and renders again, as Next does when a navigation commits. */
function navigateTo(rerender: () => void, pathname: string, search = '') {
  route.pathname = pathname;
  route.search = search;
  rerender();
}

beforeEach(() => {
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
      expect(screen.queryByRole('status')).toBeNull();
    });
  });

  describe('Given a click on a link', () => {
    it('should start for an internal link to another page', () => {
      const { link } = renderWithLink();

      fireEvent.click(link);

      expect(isShown()).toBe(true);
      expect(screen.getByRole('status', { name: 'Loading' })).toBeInTheDocument();
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

      expect(isShown()).toBe(false);
    });

    it('should stop when only the query string changes', () => {
      const { link, rerender } = renderWithLink({}, '/?q=cat');
      fireEvent.click(link);

      navigateTo(rerender, '/', 'q=cat');

      expect(isShown()).toBe(false);
    });

    it('should be able to start again after it stopped', () => {
      const { link, rerender } = renderWithLink();
      fireEvent.click(link);
      navigateTo(rerender, '/about');

      fireEvent.click(link);

      expect(isShown()).toBe(true);
    });

    it('should stop after the safety timeout when the URL never changes', () => {
      vi.useFakeTimers();
      const { link } = renderWithLink();
      fireEvent.click(link);

      act(() => {
        vi.advanceTimersByTime(NAVIGATION_TIMEOUT_MS - 1);
      });
      expect(isShown()).toBe(true);

      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(isShown()).toBe(false);
    });

    it('should restart the timeout when another link is clicked', () => {
      vi.useFakeTimers();
      const { link } = renderWithLink();
      fireEvent.click(link);
      act(() => {
        vi.advanceTimersByTime(NAVIGATION_TIMEOUT_MS - 1000);
      });

      fireEvent.click(link);
      act(() => {
        vi.advanceTimersByTime(NAVIGATION_TIMEOUT_MS - 1000);
      });

      expect(isShown()).toBe(true);
    });

    it('should stop on a page error', () => {
      const { link } = renderWithLink();
      fireEvent.click(link);

      act(() => {
        window.dispatchEvent(new Event('error'));
      });

      expect(isShown()).toBe(false);
    });

    it('should stop on an unhandled promise rejection', () => {
      const { link } = renderWithLink();
      fireEvent.click(link);

      act(() => {
        window.dispatchEvent(new Event('unhandledrejection'));
      });

      expect(isShown()).toBe(false);
    });

    it('should stop when the page is restored from the back-forward cache', () => {
      const { link } = renderWithLink();
      fireEvent.click(link);

      act(() => {
        window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
      });

      expect(isShown()).toBe(false);
    });

    it('should keep going on a normal pageshow', () => {
      const { link } = renderWithLink();
      fireEvent.click(link);

      act(() => {
        window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: false }));
      });

      expect(isShown()).toBe(true);
    });
  });

  describe('Given the indicator is showing', () => {
    it('should not block clicks and should not move the layout', () => {
      const { link } = renderWithLink();
      fireEvent.click(link);

      const bar = screen.getByTestId(INDICATOR);
      const spinner = screen.getByRole('status').parentElement as HTMLElement;

      for (const element of [bar, spinner]) {
        expect(element).toHaveClass('pointer-events-none', 'fixed');
      }
      expect(bar).toHaveAttribute('aria-hidden', 'true');
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
      vi.useFakeTimers();
      fireEvent.submit(form);
      act(() => {
        vi.advanceTimersByTime(0);
      });
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
      vi.useFakeTimers();

      fireEvent(
        screen.getByTestId('form'),
        new SubmitEvent('submit', { bubbles: true, cancelable: true, submitter })
      );
      act(() => {
        vi.advanceTimersByTime(0);
      });

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
      vi.useFakeTimers();

      fireEvent(
        screen.getByTestId('form'),
        new SubmitEvent('submit', {
          bubbles: true,
          cancelable: true,
          submitter: screen.getByTestId('new-tab'),
        })
      );
      act(() => {
        vi.advanceTimersByTime(0);
      });
      expect(isShown()).toBe(false);

      fireEvent(
        screen.getByTestId('form'),
        new SubmitEvent('submit', {
          bubbles: true,
          cancelable: true,
          submitter: screen.getByTestId('go'),
        })
      );
      act(() => {
        vi.advanceTimersByTime(0);
      });
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
      vi.useFakeTimers();

      fireEvent.submit(screen.getByTestId('form'));
      act(() => {
        vi.advanceTimersByTime(0);
      });

      expect(isShown()).toBe(false);
    });

    it('should ignore a submit that is not from a form', () => {
      render(<NavigationProgress />);
      vi.useFakeTimers();

      fireEvent.submit(document.body);
      act(() => {
        vi.advanceTimersByTime(0);
      });

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
      } finally {
        vi.stubGlobal('window', original);
      }
    });
  });

  describe('Given the component unmounts', () => {
    it('should stop listening', () => {
      const { link, unmount } = renderWithLink();
      unmount();
      link.addEventListener('click', (event) => event.preventDefault());
      document.body.append(link);

      fireEvent.click(link);

      expect(isShown()).toBe(false);
    });
  });
});
