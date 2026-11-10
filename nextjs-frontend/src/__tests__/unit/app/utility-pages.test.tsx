import { describe, expect, it, vi, beforeEach } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { render, screen, fireEvent } from '@testing-library/react';

const captureException = vi.fn();
vi.mock('@sentry/nextjs', () => ({ captureException: (e: unknown) => captureException(e) }));
vi.mock('@/app/globals.css', () => ({}));

import NotFound from '@/app/not-found';
import ErrorPage from '@/app/error';
import GlobalError from '@/app/global-error';
import { RouteSkeleton as Loading } from '@/components/site/route-skeleton';
import ContactPage from '@/app/(public)/contact/page';

/** system and utility pages follow the tokens and the plan 5.8 field pattern. */

const err = Object.assign(new Error('boom'), { digest: 'abc123' });

beforeEach(() => captureException.mockClear());

describe('not-found', () => {
  it('shows a heading and a link home with a 44px target', () => {
    render(<NotFound />);

    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
    const home = screen.getByRole('link', { name: /home/i });
    expect(home).toHaveAttribute('href', '/');
    expect(home.className).toMatch(/h-1[12]/);
  });

  it('uses tokens, not raw colours', () => {
    const { container } = render(<NotFound />);

    expect(container.innerHTML).not.toMatch(/#[0-9a-f]{3,6}|rgb\(|text-white|bg-white/i);
  });
});

describe('error', () => {
  it('reports to Sentry once and offers Retry and a link home', () => {
    const reset = vi.fn();
    render(<ErrorPage error={err} reset={reset} />);

    expect(captureException).toHaveBeenCalledWith(err);
    fireEvent.click(screen.getByRole('button', { name: /try again|retry/i }));
    expect(reset).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('link', { name: /home/i })).toHaveAttribute('href', '/');
  });

  it('uses the crit tokens and shows the digest, without role=alert (focus announces it)', () => {
    const { container } = render(<ErrorPage error={err} reset={vi.fn()} />);

    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText(/Error ID: abc123/)).toBeInTheDocument();
    expect(container.innerHTML).toMatch(/crit/);
    expect(container.innerHTML).not.toMatch(/#[0-9a-f]{3,6}|destructive\/50/i);
  });

  it('does not add a main landmark, so it can render inside a shell that has one', () => {
    render(<ErrorPage error={err} reset={vi.fn()} />);

    expect(screen.queryByRole('main')).toBeNull();
  });

  it('moves focus to the heading', () => {
    render(<ErrorPage error={err} reset={vi.fn()} />);

    expect(screen.getByRole('heading', { level: 1 })).toHaveFocus();
  });

  it('never leaks the error message to users', () => {
    render(<ErrorPage error={err} reset={vi.fn()} />);

    expect(screen.queryByText('boom')).toBeNull();
  });
});

describe('global-error', () => {
  // The component owns <html> and <body>, so the shell is checked from static markup.
  it('renders its own html shell with lang, body and the digest', () => {
    const html = renderToStaticMarkup(<GlobalError error={err} reset={vi.fn()} />);

    expect(html).toMatch(/^<html lang="en">/);
    expect(html).toMatch(/<body[ >]/);
    expect(html).toContain('Error ID: abc123');
    expect(html).not.toMatch(/role="alert"|style="/);
  });

  it('reports once, focuses the heading, and offers Retry and a plain home link', () => {
    // React warns that <html> cannot sit inside a <div>; expected for this boundary.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const reset = vi.fn();
    render(<GlobalError error={err} reset={reset} />);
    consoleError.mockRestore();

    expect(captureException).toHaveBeenCalledTimes(1);
    expect(captureException).toHaveBeenCalledWith(err);
    expect(screen.getByRole('heading', { level: 1 })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: /try again|retry/i }));
    expect(reset).toHaveBeenCalledTimes(1);
    // a plain anchor: the app router may be what failed
    expect(screen.getByRole('link', { name: /home/i })).toHaveAttribute('href', '/');
  });
});

describe('loading', () => {
  it('is a busy region with skeleton blocks and no spinner', () => {
    const { container } = render(<Loading />);

    const region = screen.getByRole('status');
    expect(region).toHaveAttribute('aria-busy', 'true');
    expect(screen.queryByRole('main')).toBeNull();
    expect(region).toHaveAccessibleName(/loading/i);
    expect(region).toHaveTextContent('Loading');
    expect(screen.getByText('Loading')).toHaveClass('sr-only');
    expect(container.querySelectorAll('[aria-hidden="true"].bg-secondary').length).toBeGreaterThan(
      1
    );
    expect(container.innerHTML).not.toMatch(/animate-spin/);
  });
});

describe('contact page', () => {
  async function renderPage(plan?: string) {
    render(await ContactPage({ searchParams: Promise.resolve({ plan }) }));
  }

  it('keeps the form disabled and sends nothing', async () => {
    await renderPage();

    expect(screen.getByRole('group', { name: /unavailable/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /send message/i })).toBeDisabled();
    expect(document.body.innerHTML).not.toMatch(/mailto:|tel:|@[a-z0-9-]+\.[a-z]{2,}/i);
  });

  it('uses labels, not placeholders, and marks required fields in the label text', async () => {
    await renderPage();

    for (const el of document.querySelectorAll('input, textarea')) {
      expect(el).not.toHaveAttribute('placeholder');
    }
    expect(screen.getByLabelText(/^email \(required\)/i)).toBeRequired();
    expect(screen.getByLabelText(/company \(optional\)/i)).not.toBeRequired();
  });

  it('gives fields a hint linked by aria-describedby', async () => {
    await renderPage();

    const email = screen.getByLabelText(/^email/i);
    const hint = document.getElementById(email.getAttribute('aria-describedby') ?? '');
    expect(hint).not.toBeNull();
    expect(hint?.className).toMatch(/text-muted-foreground/);
  });

  it('ignores a repeated plan parameter instead of throwing', async () => {
    render(
      await ContactPage({
        searchParams: Promise.resolve({ plan: ['a', 'b'] as unknown as string }),
      })
    );

    expect(screen.queryByLabelText(/interested plan/i)).toBeNull();
  });

  it('links retailers to the "List your free sample" page without adding any contact detail (FREEJ-55)', async () => {
    await renderPage();

    expect(screen.getByRole('link', { name: 'List your free sample' })).toHaveAttribute(
      'href',
      '/partners'
    );
    expect(document.body.innerHTML).not.toMatch(/mailto:|tel:|@[a-z0-9-]+\.[a-z]{2,}/i);
  });

  it('shows the selected plan, capitalised', async () => {
    await renderPage('pro');

    expect(screen.getByLabelText(/interested plan/i)).toHaveValue('Pro');
  });
});
