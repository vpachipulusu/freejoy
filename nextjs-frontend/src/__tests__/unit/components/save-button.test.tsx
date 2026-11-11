import { renderToString } from 'react-dom/server';
import { hydrateRoot } from 'react-dom/client';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { OfferCard } from '@/components/catalogue/offer-card';
import { OfferHero } from '@/components/offer/offer-hero';
import { SaveButton } from '@/components/saved/save-button';
import { CATEGORIES, MERCHANTS, OFFERS } from '@/lib/catalogue/fixtures';
import { SAVED_STORAGE_KEY } from '@/lib/saved/saved-store';

const offer = OFFERS[0];
const merchant = MERCHANTS.find((item) => item.slug === offer.merchantSlug)!;
const category = CATEGORIES.find((item) => item.slug === offer.categorySlug)!;

function storedSlugs(): string[] {
  const raw = window.localStorage.getItem(SAVED_STORAGE_KEY);
  return raw === null ? [] : (JSON.parse(raw) as { slugs: string[] }).slugs;
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('SaveButton', () => {
  it('is a real button, not pressed at first, named after the offer', () => {
    render(<SaveButton slug="glow-cream" title="Glow cream sample" />);

    const button = screen.getByRole('button', { name: 'Save Glow cream sample' });
    expect(button.tagName).toBe('BUTTON');
    expect(button).toHaveAttribute('type', 'button');
    expect(button).toHaveAttribute('aria-pressed', 'false');
  });

  it('saves on click, then says so in its pressed state and its name, and stores only the slug', async () => {
    const user = setupUser();
    render(<SaveButton slug="glow-cream" title="Glow cream sample" />);

    await user.click(screen.getByRole('button', { name: 'Save Glow cream sample' }));

    const pressed = screen.getByRole('button', { name: 'Save Glow cream sample', pressed: true });
    expect(pressed).toHaveAttribute('aria-pressed', 'true');
    expect(storedSlugs()).toEqual(['glow-cream']);
    expect(window.localStorage.getItem(SAVED_STORAGE_KEY)).not.toContain('Glow cream sample');
  });

  it('keeps one constant name, saved or not: aria-pressed carries the state, the label never flips', async () => {
    const user = setupUser();
    render(<SaveButton slug="glow-cream" title="Glow cream sample" />);
    const button = screen.getByRole('button');

    expect(button).toHaveAttribute('aria-label', 'Save Glow cream sample');
    await user.click(button);

    expect(button).toHaveAttribute('aria-label', 'Save Glow cream sample');
    expect(button).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: /^Remove/ })).not.toBeInTheDocument();
  });

  it('names its own hover group, so hovering the card does not light the heart', () => {
    render(<SaveButton slug="glow-cream" title="Glow cream sample" />);
    const button = screen.getByRole('button');
    const disc = button.querySelector('span')!;

    expect(button).toHaveClass('group/save');
    expect(disc.className).toContain('group-hover/save:bg-muted');
    expect(disc.className).not.toMatch(/(^|\s)group-hover:/);
  });

  it('removes the offer on a second click', async () => {
    const user = setupUser();
    render(<SaveButton slug="glow-cream" title="Glow cream sample" />);

    await user.click(screen.getByRole('button', { name: /^Save/ }));
    await user.click(screen.getByRole('button', { name: /^Save/, pressed: true }));

    expect(screen.getByRole('button', { name: 'Save Glow cream sample' })).toHaveAttribute(
      'aria-pressed',
      'false'
    );
    expect(storedSlugs()).toEqual([]);
  });

  it('toggles from the keyboard with Enter and with Space', async () => {
    const user = setupUser();
    render(<SaveButton slug="glow-cream" title="Glow cream sample" />);

    await user.tab();
    const button = screen.getByRole('button');
    expect(button).toHaveFocus();

    await user.keyboard('{Enter}');
    expect(button).toHaveAttribute('aria-pressed', 'true');
    await user.keyboard(' ');
    expect(button).toHaveAttribute('aria-pressed', 'false');
    expect(button).toHaveFocus();
  });

  it('shows a saved offer as saved on first render in the browser', () => {
    window.localStorage.setItem(SAVED_STORAGE_KEY, JSON.stringify({ v: 1, slugs: ['glow-cream'] }));
    render(<SaveButton slug="glow-cream" title="Glow cream sample" />);

    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true');
  });

  it('keeps every heart for the same offer in step', async () => {
    const user = setupUser();
    render(
      <>
        <SaveButton slug="glow-cream" title="Glow cream sample" />
        <SaveButton slug="glow-cream" title="Glow cream sample" />
        <SaveButton slug="other-offer" title="Other offer" />
      </>
    );

    await user.click(screen.getAllByRole('button', { name: /^Save Glow/ })[0]);

    expect(
      screen.getAllByRole('button', { name: 'Save Glow cream sample', pressed: true })
    ).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Save Other offer' })).toHaveAttribute(
      'aria-pressed',
      'false'
    );
  });

  it('follows a change made in another tab', () => {
    render(<SaveButton slug="glow-cream" title="Glow cream sample" />);

    act(() => {
      window.localStorage.setItem(
        SAVED_STORAGE_KEY,
        JSON.stringify({ v: 1, slugs: ['glow-cream'] })
      );
      window.dispatchEvent(new StorageEvent('storage', { key: SAVED_STORAGE_KEY }));
    });

    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true');
  });

  it('is at least 44 by 44 pixels, with a heart shape that changes (not only its colour)', async () => {
    const user = setupUser();
    render(<SaveButton slug="glow-cream" title="Glow cream sample" />);
    const button = screen.getByRole('button');

    expect(button).toHaveClass('h-11', 'w-11');
    const heart = () => button.querySelector('svg')!;
    expect(heart()).toHaveAttribute('aria-hidden', 'true');
    expect(heart().getAttribute('class')).not.toContain('fill-cta');

    await user.click(button);

    expect(heart().getAttribute('class')).toContain('fill-cta');
  });

  it('animates only for people who have not asked for reduced motion', () => {
    render(<SaveButton slug="glow-cream" title="Glow cream sample" />);
    const classes = screen.getByRole('button').className.split(/\s+/);

    const motion = classes.filter((name) => /(^|:)(transition|duration|animate|scale)/.test(name));
    expect(motion.length).toBeGreaterThan(0);
    expect(motion.every((name) => name.startsWith('motion-safe:'))).toBe(true);
  });

  it('draws no focus style of its own, so the one global outline shows', () => {
    render(<SaveButton slug="glow-cream" title="Glow cream sample" />);

    expect(screen.getByRole('button').className).not.toMatch(/outline-|ring/);
  });

  it('renders the same markup on the server as the first client render (nothing saved)', () => {
    window.localStorage.setItem(SAVED_STORAGE_KEY, JSON.stringify({ v: 1, slugs: ['glow-cream'] }));
    const html = renderToString(<SaveButton slug="glow-cream" title="Glow cream sample" />);

    expect(html).toContain('aria-pressed="false"');
    expect(html).toContain('aria-label="Save Glow cream sample"');
    expect(html).not.toContain('Remove');
  });

  it('hydrates server markup without a mismatch, then shows the saved state', async () => {
    window.localStorage.setItem(SAVED_STORAGE_KEY, JSON.stringify({ v: 1, slugs: ['glow-cream'] }));
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    const container = document.createElement('div');
    document.body.appendChild(container);
    container.innerHTML = renderToString(
      <SaveButton slug="glow-cream" title="Glow cream sample" />
    );

    let root!: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, <SaveButton slug="glow-cream" title="Glow cream sample" />);
    });

    expect(errors).not.toHaveBeenCalled();
    expect(container.querySelector('button')).toHaveAttribute('aria-pressed', 'true');
    act(() => root.unmount());
    container.remove();
  });

  it('still toggles for this page when the browser refuses to store anything', async () => {
    const user = setupUser();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError');
    });
    render(<SaveButton slug="glow-cream" title="Glow cream sample" />);

    await user.click(screen.getByRole('button', { name: /^Save/ }));
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: /^Save/, pressed: true }));
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'false');

    // Leave the shared store as storage-backed for the tests that follow.
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: null }));
    });
  });
});

describe('the heart on an offer card', () => {
  it('is a button named after the offer, outside the card link', () => {
    render(<OfferCard offer={offer} merchant={merchant} />);

    const heart = screen.getByRole('button', { name: `Save ${offer.title}` });
    const link = screen.getByRole('link', { name: offer.title });
    expect(heart.closest('a')).toBeNull();
    expect(link.querySelector('button')).toBeNull();
    expect(link.contains(heart)).toBe(false);
  });

  it('sits above the stretched link so a click reaches it, and the card has one link', () => {
    const { container } = render(<OfferCard offer={offer} merchant={merchant} />);

    expect(screen.getByRole('button', { name: /^Save/ })).toHaveClass('absolute', 'z-10');
    expect(container.querySelectorAll('a')).toHaveLength(1);
  });

  it('saves without following the link', async () => {
    const user = setupUser();
    render(<OfferCard offer={offer} merchant={merchant} />);

    await user.click(screen.getByRole('button', { name: `Save ${offer.title}` }));

    expect(storedSlugs()).toEqual([offer.slug]);
  });

  it('keeps the demo badge clear of the heart', () => {
    render(<OfferCard offer={offer} merchant={merchant} />);

    expect(screen.getByText('Demo').className).toMatch(/bottom-3/);
    expect(screen.getByText('Demo').className).not.toMatch(/(^|\s)(sm:)?top-3/);
  });
});

describe('the heart on the offer page', () => {
  it('is in the hero, named after the offer', () => {
    render(<OfferHero offer={offer} merchant={merchant} category={category} ended={false} />);

    expect(screen.getByRole('button', { name: `Save ${offer.title}` })).toHaveAttribute(
      'aria-pressed',
      'false'
    );
  });

  it('is still offered on an ended offer, so it can be removed from the list', () => {
    render(<OfferHero offer={offer} merchant={merchant} category={category} ended />);

    expect(screen.getByRole('button', { name: `Save ${offer.title}` })).toBeInTheDocument();
  });
});
