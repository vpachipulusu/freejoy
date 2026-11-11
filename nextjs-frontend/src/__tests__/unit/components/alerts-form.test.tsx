import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';

const mocks = vi.hoisted(() => ({ subscribe: vi.fn() }));
vi.mock('@/app/actions/alerts-actions', () => ({ subscribeToAlertsAction: mocks.subscribe }));

import { AlertsForm } from '@/components/alerts/alerts-form';
import { ALERTS_CONSENT_TEXT } from '@/lib/alerts/consent';
import { ALERTS_FAILURE_MESSAGES } from '@/lib/alerts/outcomes';

const CATEGORIES = [
  { slug: 'beauty', name: 'Beauty & skincare' },
  { slug: 'food-drink', name: 'Food & drink' },
  { slug: 'pets', name: 'Pets' },
];

const INBOX_PANEL = /check your inbox/i;
const UNAVAILABLE = {
  success: false,
  code: 'unavailable',
  error: ALERTS_FAILURE_MESSAGES.unavailable,
} as const;

function setup() {
  const user = setupUser();
  render(<AlertsForm categories={CATEGORIES} live />);
  const form = {
    user,
    email: () => screen.getByLabelText(/email address/i),
    consent: () => screen.getByRole('checkbox', { name: /i agree to receive freebie emails/i }),
    submit: () => screen.getByRole('button', { name: /send me free alerts|sending/i }),
    async fillValid() {
      await user.type(form.email(), 'sam@example.com');
      await user.click(screen.getByLabelText('Food & drink'));
      await user.click(screen.getByLabelText('Weekly round-up'));
      await user.click(form.consent());
    },
  };
  return form;
}

beforeEach(() => {
  mocks.subscribe.mockReset();
  mocks.subscribe.mockResolvedValue({ success: true });
});

describe('AlertsForm keyboard focus', () => {
  it('uses no ring for the focus of the hidden checkbox and radio inputs', () => {
    render(<AlertsForm categories={CATEGORIES} live />);
    const card = screen.getByLabelText('Weekly round-up').closest('label')!;

    expect(card.className).not.toMatch(/(^|[\s:])ring(-|\s|$)/);
  });

  it('leaves the card outline to the focus-proxy rule in globals.css (no per-component outline utilities)', () => {
    render(<AlertsForm categories={CATEGORIES} live />);
    const cards = [
      screen.getByLabelText('Beauty & skincare').closest('label')!,
      screen.getByLabelText('Daily highlights').closest('label')!,
    ];

    for (const card of cards) {
      expect(card.className).not.toMatch(/outline|has-\[:focus/);
      expect(card.querySelector(':scope > input.sr-only')).not.toBeNull();
    }
  });

  it.each([
    ['checkbox', 'Beauty & skincare'],
    ['radio', 'Daily highlights'],
  ])(
    'gives the %s a peer-focus-visible outline for browsers without :has(), off where :has() works',
    (_type, label) => {
      render(<AlertsForm categories={CATEGORIES} live />);
      const input = screen.getByLabelText(label);
      const fallback = input.nextElementSibling!;

      expect(input).toHaveClass('peer', 'sr-only');
      expect(fallback).toHaveAttribute('aria-hidden', 'true');
      // One overlay on the border box (-inset-px), so the fallback ring matches the :has() outline around the card.
      expect(fallback).toHaveClass('pointer-events-none', 'absolute', '-inset-px');
      expect(fallback.closest('label')).toHaveClass('relative');
      // Whole class tokens: tailwind-merge must not have dropped the plain `outline` (style) next to `outline-[3px]`.
      const classes = fallback.className.split(/\s+/);
      for (const utility of ['outline', 'outline-[3px]', 'outline-offset-2', 'outline-ring']) {
        expect(classes).toContain(
          `supports-[not_(selector(:has(*)))]:peer-focus-visible:${utility}`
        );
      }
      expect(fallback.className).not.toMatch(/(^|[\s:])ring(-|\s|$)/);
    }
  );
});

describe('AlertsForm promises', () => {
  it('says how to stop, and makes no "no spam", "anytime" or sold-lists claim', () => {
    const { container } = render(<AlertsForm categories={CATEGORIES} live />);

    expect(container).toHaveTextContent('Unsubscribe from any email to stop them.');
    expect(container).toHaveTextContent('Unsubscribe in one click from any email');
    expect(container.textContent).not.toMatch(/no spam|anytime|sold/i);
  });

  it('keeps the stored consent text exactly as it is', () => {
    expect(ALERTS_CONSENT_TEXT).toBe(
      'I agree to receive freebie emails from Freejoy. I can unsubscribe at any time. See the privacy policy.'
    );
  });
});

describe('AlertsForm while alerts are not open', () => {
  it('shows a plain notice instead of a form, and never calls the server', async () => {
    render(<AlertsForm categories={CATEGORIES} live={false} />);

    expect(screen.getByText(/alerts aren.t open yet/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Browse freebies' })).toHaveAttribute(
      'href',
      '/freebies'
    );
    expect(screen.queryByRole('button', { name: /send me free alerts/i })).toBeNull();
    expect(screen.queryByLabelText(/email address/i)).toBeNull();
    expect(screen.queryByText(/something went wrong/i)).toBeNull();
    expect(mocks.subscribe).not.toHaveBeenCalled();
  });
});

describe('AlertsForm validation', () => {
  it('shows an error and sends nothing for an invalid email', async () => {
    const form = setup();
    await form.user.type(form.email(), 'not-an-email');
    await form.user.click(screen.getByLabelText('Beauty & skincare'));
    await form.user.click(form.consent());

    await form.user.click(form.submit());

    expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument();
    expect(form.email()).toHaveAttribute('aria-invalid', 'true');
    expect(form.email()).toHaveAccessibleDescription('Enter a valid email address.');
    expect(mocks.subscribe).not.toHaveBeenCalled();
  });

  it('asks for at least one category before sending', async () => {
    const form = setup();
    await form.user.type(form.email(), 'sam@example.com');
    await form.user.click(form.consent());

    await form.user.click(form.submit());

    expect(await screen.findByText('Choose at least one category.')).toBeInTheDocument();
    expect(mocks.subscribe).not.toHaveBeenCalled();
  });

  it('requires consent before sending', async () => {
    const form = setup();
    await form.user.type(form.email(), 'sam@example.com');
    await form.user.click(screen.getByLabelText('Pets'));

    await form.user.click(form.submit());

    expect(await screen.findByText(/please agree to receive emails/i)).toBeInTheDocument();
    expect(form.consent()).toHaveAttribute('aria-invalid', 'true');
    expect(mocks.subscribe).not.toHaveBeenCalled();
  });
});

describe('AlertsForm before submitting', () => {
  it('has no demo notice, defaults to daily and starts with an empty address', () => {
    setup();

    expect(screen.queryByText(/design preview|not live yet/i)).toBeNull();
    expect(screen.getByLabelText('Daily highlights')).toBeChecked();
    expect(screen.getByLabelText('Weekly round-up')).not.toBeChecked();
    expect(screen.getByLabelText(/email address/i)).toHaveValue('');
  });

  it('shows exactly the consent wording that the server stores', () => {
    const form = setup();

    expect(form.consent().closest('label')?.textContent?.trim()).toBe(ALERTS_CONSENT_TEXT);
    expect(screen.getByRole('link', { name: 'privacy policy' })).toHaveAttribute(
      'href',
      '/privacy'
    );
  });
});

describe('AlertsForm submitting', () => {
  it('sends the answers to the server action and asks the visitor to check their inbox', async () => {
    const form = setup();
    await form.fillValid();

    await form.user.click(form.submit());

    expect(mocks.subscribe).toHaveBeenCalledWith({
      email: 'sam@example.com',
      categories: ['food-drink'],
      frequency: 'weekly',
      consent: true,
    });
    expect(await screen.findByText(INBOX_PANEL)).toBeInTheDocument();
    expect(screen.getByText(/sam@example\.com is new to our alerts/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing starts until you do/i)).toBeInTheDocument();
    expect(
      screen.getByText(/if it has not arrived after a few minutes, you can submit the form again/i)
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Browse freebies' })).toHaveAttribute(
      'href',
      '/freebies'
    );
    expect(screen.queryByRole('button', { name: /send me free alerts/i })).toBeNull();
  });

  it('does not say whether the address was already subscribed', async () => {
    const form = setup();
    await form.fillValid();

    await form.user.click(form.submit());

    const panel = await screen.findByText(INBOX_PANEL);
    expect(panel.parentElement?.textContent).not.toMatch(/already (subscribed|on)|welcome back/i);
  });

  it('marks the button aria-disabled while the request runs, keeps focus on it and ignores a second press', async () => {
    let finish: (value: { success: true }) => void = () => {};
    mocks.subscribe.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const form = setup();
    await form.fillValid();

    await form.user.click(form.submit());
    const sending = screen.getByRole('button', { name: 'Sending…' });
    await form.user.click(sending);

    expect(sending).toHaveAttribute('aria-disabled', 'true');
    expect(sending).not.toBeDisabled();
    expect(sending).toHaveFocus();
    expect(mocks.subscribe).toHaveBeenCalledTimes(1);
    finish({ success: true });
    expect(await screen.findByText(INBOX_PANEL)).toBeInTheDocument();
  });

  it('keeps keyboard focus on the button after a failed submit', async () => {
    mocks.subscribe.mockResolvedValueOnce(UNAVAILABLE);
    const form = setup();
    await form.fillValid();

    await form.user.click(form.submit());
    await screen.findByRole('alert');

    expect(document.activeElement).toBe(form.submit());
    await waitFor(() => expect(form.submit()).not.toHaveAttribute('aria-disabled'));
  });

  it('puts the failure alert below the button and outside the polite live region', async () => {
    mocks.subscribe.mockResolvedValueOnce(UNAVAILABLE);
    const form = setup();
    await form.fillValid();

    await form.user.click(form.submit());

    const alert = await screen.findByRole('alert');
    expect(alert.closest('[aria-live]')).toBeNull();
    expect(
      form.submit().compareDocumentPosition(alert) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });

  it('shows a curated error, keeps the answers and succeeds on retry', async () => {
    mocks.subscribe.mockResolvedValueOnce(UNAVAILABLE);
    const form = setup();
    await form.fillValid();

    await form.user.click(form.submit());

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(ALERTS_FAILURE_MESSAGES.unavailable);
    expect(form.email()).toHaveValue('sam@example.com');
    await waitFor(() => expect(form.email()).toBeEnabled());
    expect(screen.getByLabelText('Food & drink')).toBeChecked();
    await waitFor(() => expect(form.submit()).not.toHaveAttribute('aria-disabled'));

    await form.user.click(form.submit());

    expect(await screen.findByText(INBOX_PANEL)).toBeInTheDocument();
    expect(mocks.subscribe).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows the curated message when the request itself throws', async () => {
    mocks.subscribe.mockRejectedValueOnce(new Error('network down: secret detail'));
    const form = setup();
    await form.fillValid();

    await form.user.click(form.submit());

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(ALERTS_FAILURE_MESSAGES.unavailable);
    expect(alert.textContent).not.toMatch(/secret detail/);
  });

  it('restores the form with the typed email after "Use a different address"', async () => {
    const form = setup();
    await form.fillValid();
    await form.user.click(form.submit());
    await screen.findByText(INBOX_PANEL);

    await form.user.click(screen.getByRole('button', { name: 'Use a different address' }));

    expect(form.email()).toHaveValue('sam@example.com');
    expect(form.email()).toBeEnabled();
    expect(screen.getByLabelText('Food & drink')).toBeChecked();
    expect(form.submit()).toBeInTheDocument();
    expect(screen.queryByText(INBOX_PANEL)).toBeNull();
  });
});
