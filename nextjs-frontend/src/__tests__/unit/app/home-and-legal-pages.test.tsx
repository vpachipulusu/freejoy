import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';

vi.mock('@/app/globals.css', () => ({}));

import HomePage, { generateMetadata as homeGenerateMetadata } from '@/app/(public)/page';
import { HomeSections } from '@/components/home/home-sections';
import AboutPage, {
  dynamic as aboutDynamic,
  metadata as aboutMetadata,
} from '@/app/(public)/about/page';
import PrivacyPage, { metadata as privacyMetadata } from '@/app/(public)/privacy/page';
import CookiesPage, { metadata as cookiesMetadata } from '@/app/(public)/cookies/page';
import TermsPage, { metadata as termsMetadata } from '@/app/(public)/terms/page';
import AffiliatePage, {
  metadata as affiliateMetadata,
} from '@/app/(public)/affiliate-disclosure/page';
import { DRAFT_BANNER_TEXT } from '@/components/legal/legal-page';
import ContactPage, { metadata as contactMetadata } from '@/app/(public)/contact/page';

const DRAFT_BANNER = DRAFT_BANNER_TEXT;

const LEGAL_PAGES = [
  { name: 'privacy', Page: PrivacyPage, metadata: privacyMetadata },
  { name: 'cookies', Page: CookiesPage, metadata: cookiesMetadata },
  { name: 'terms', Page: TermsPage, metadata: termsMetadata },
  { name: 'affiliate disclosure', Page: AffiliatePage, metadata: affiliateMetadata },
];

/** Phrases that would claim compliance, registration or approval the site cannot yet back up. */
const COMPLIANCE_CLAIMS =
  /fully compliant|(gdpr|pecr|ico|asa|cma)[- ]compliant|compliant with|ico[- ]registered|registered with the ico|registration number|approved by|certified|verified by|guaranteed|completely free|100% free/i;

/**
 * The page streams the catalogue sections through Suspense, so render the page shell and the
 * resolved sections side by side. `container` is the whole document body.
 */
async function renderHome() {
  render(await HomePage());
  render(await HomeSections({ demo: true }));
  return { container: document.body };
}

describe('homepage', () => {
  it('shows the display heading as the only h1', async () => {
    await renderHome();

    const headings = screen.getAllByRole('heading', { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent(/small treats\.\s*big free energy\./i);
  });

  it('links each of the six categories to its category page', async () => {
    const { container } = await renderHome();

    const links = container.querySelectorAll('a[href^="/category/"]');
    expect(links).toHaveLength(6);
  });

  it('shows eight offer cards under "Freshly found" with the sample-listing note', async () => {
    await renderHome();

    const section = screen.getByRole('region', { name: /freshly found/i });
    expect(within(section).getAllByRole('article')).toHaveLength(8);
    expect(
      within(section).getByText(/sample listings for the design preview/i)
    ).toBeInTheDocument();
  });

  it('offers the hero CTAs to /freebies and the how-it-works anchor', async () => {
    await renderHome();

    expect(screen.getByRole('link', { name: 'Explore freebies' })).toHaveAttribute(
      'href',
      '/freebies'
    );
    expect(screen.getByRole('link', { name: 'How it works' })).toHaveAttribute(
      'href',
      '/about#how-it-works'
    );
  });

  it('describes freshness as regular and uses no counts in the trust strip', async () => {
    const { container } = await renderHome();

    expect(screen.getByText('Design preview with sample listings')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/every day|daily dose|checked daily/i);
    const strip = screen.getByRole('region', { name: /how we list offers/i });
    expect(within(strip).getAllByRole('listitem')).toHaveLength(4);
    expect(strip.textContent).not.toMatch(/\d/);
  });

  it('links the sign-up band to /alerts without collecting an email address', async () => {
    const { container } = await renderHome();

    expect(screen.getByRole('link', { name: 'Get free alerts' })).toHaveAttribute(
      'href',
      '/alerts'
    );
    expect(container.querySelector('form')).toBeNull();
    expect(container.querySelector('input[type="email"]')).toBeNull();
    expect(screen.getByRole('link', { name: 'privacy details' })).toHaveAttribute(
      'href',
      '/privacy'
    );
  });

  it('contains no external links', async () => {
    const { container } = await renderHome();

    expect(container.querySelectorAll('a[href^="http"]')).toHaveLength(0);
  });

  it('sets the canonical url and title in metadata', async () => {
    const homeMetadata = await homeGenerateMetadata();

    expect(homeMetadata.title).toBe('Freejoy – UK freebies, samples and offers');
    expect(homeMetadata.robots).toEqual({ index: false });
    expect(homeMetadata.alternates?.canonical).toBe('/');
  });
});

describe.each(LEGAL_PAGES)('$name page', ({ Page, metadata }) => {
  it('shows the draft banner, an h1 and the reviewer checklist', () => {
    render(<Page />);

    expect(screen.getByText(DRAFT_BANNER)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'For the reviewer to confirm' })
    ).toBeInTheDocument();
  });

  it('is kept out of search engines while draft', () => {
    expect(metadata.robots).toEqual({ index: false });
  });

  it('contains no external links', () => {
    const { container } = render(<Page />);

    expect(container.querySelectorAll('a[href^="http"]')).toHaveLength(0);
  });
});

describe('cookies page', () => {
  it('states the intention to set no non-essential cookies, pending review', () => {
    render(<CookiesPage />);

    expect(
      screen.getByText(/intended to set no non-essential cookies and run no analytics/i)
    ).toBeInTheDocument();
  });
});

describe('affiliate disclosure page', () => {
  it('is true whether or not a real offer is live: no "no outbound links" or "planned" wording', () => {
    const { container } = render(<AffiliatePage />);

    expect(container.textContent).not.toMatch(/no outbound links/i);
    expect(container.textContent).not.toMatch(/planned/i);
    expect(container.textContent).not.toMatch(/none of this is live/i);
    expect(container.textContent).not.toMatch(/no commission can be earned/i);
    expect(container.textContent).not.toMatch(/nothing on the site earns/i);
  });

  it('explains the /go/ redirect, the paid-link marking and the Ad label', () => {
    const { container } = render(<AffiliatePage />);

    expect(container.textContent).toContain('/go/');
    expect(container.textContent).toMatch(/marked as a paid link for search engines/i);
    expect(container.textContent).toMatch(/“Ad”/);
    expect(container.textContent).toMatch(/pays Freejoy a commission/i);
    expect(container.textContent).toMatch(/paid by the brand, not by you/i);
    expect(container.textContent).not.toMatch(/rel=|sponsored nofollow/i);
  });

  it('states what a click records and what it does not', () => {
    const { container } = render(<AffiliatePage />);

    expect(container.textContent).toMatch(/name of the website you came from \(not the page\)/i);
    expect(container.textContent).toMatch(/rough device type/i);
    expect(container.textContent).toMatch(
      /does not keep your IP address, set a cookie or store any identifier/i
    );
    expect(container.textContent).toMatch(/not a count of people who claimed or bought/i);
  });

  it('stays a draft for legal review and does not hedge with "may earn"', () => {
    const { container } = render(<AffiliatePage />);

    expect(screen.getByText(DRAFT_BANNER)).toBeInTheDocument();
    expect(container.textContent).toMatch(/Not yet reviewed/);
    expect(container.textContent).not.toMatch(/may earn/i);
    expect(container.textContent).not.toMatch(/some links on freejoy are affiliate links/i);
    expect(container.textContent).not.toMatch(/never changes the terms/i);
  });
});

describe('affiliate disclosure page: sponsored listings', () => {
  it('has a plain-words section that says what sponsored means and how it is labelled', () => {
    const { container } = render(<AffiliatePage />);

    const heading = screen.getByRole('heading', { name: 'Sponsored listings' });
    expect(heading).toHaveAttribute('id', 'sponsored-listings');
    expect(container.textContent).toMatch(/the brand pays Freejoy, or gives it a benefit/i);
    expect(container.textContent).toMatch(/“Sponsored” label on its card and on its own page/);
    expect(container.textContent).toMatch(/written out in words, not shown by colour alone/i);
    expect(container.textContent).toMatch(/marked as a paid link for search engines/i);
  });

  it('no longer says paid placements are not planned, and claims nothing about sponsors being held to other standards', () => {
    const { container } = render(<AffiliatePage />);
    const section: string[] = [];
    let node = screen.getByRole('heading', { name: 'Sponsored listings' }).nextElementSibling;
    while (node && node.tagName !== 'H2') {
      section.push(node.textContent ?? '');
      node = node.nextElementSibling;
    }
    const text = section.join(' ');

    expect(container.textContent).not.toMatch(/does not plan to sell paid placements/i);
    expect(text).not.toMatch(/editorial|standards|independen|vetted|same checks/i);
  });
});

describe('about page', () => {
  it('has the how-it-works anchor with three numbered steps', async () => {
    const { container } = render(await AboutPage());

    expect(screen.getByText(DRAFT_BANNER)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('About Freejoy');
    const anchor = container.querySelector('#how-it-works');
    expect(anchor).not.toBeNull();
    expect(anchor?.nextElementSibling?.tagName).toBe('OL');
    expect(anchor?.nextElementSibling?.querySelectorAll('li')).toHaveLength(3);
    expect(container.querySelectorAll('a[href^="http"]')).toHaveLength(0);
    expect(aboutMetadata.robots).toEqual({ index: false });
  });

  it('makes no claims about counts or verification stamps', async () => {
    const { container } = render(await AboutPage());

    expect(container.textContent).not.toMatch(/\d|verified by|guaranteed/i);
  });
});

describe('about page copy follows the alerts and catalogue switches (FREEJ-42)', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  async function aboutText(env: { alerts: string; catalogue: string }) {
    vi.stubEnv('ALERTS_FORM_LIVE', env.alerts);
    vi.stubEnv('CATALOGUE_SOURCE', env.catalogue);
    const { container } = render(await AboutPage());
    return container.textContent ?? '';
  }

  it('alerts switched on: says alerts can be signed up for and never that nothing is saved', async () => {
    const text = await aboutText({ alerts: 'true', catalogue: 'api' });

    expect(text).toMatch(/sign up for email alerts with the alerts form/i);
    expect(text).toMatch(/confirmation link/i);
    expect(text).not.toMatch(/does not save anything|not open yet|preview/i);
    expect(screen.getByRole('link', { name: 'alerts form' })).toHaveAttribute('href', '/alerts');
  });

  it.each(['', 'false', 'TRUE'])(
    'alerts switched off (%j): says they are not open yet, without "preview" or a promise to save',
    async (value) => {
      const text = await aboutText({ alerts: value, catalogue: 'api' });

      expect(text).toMatch(/email alerts are not open yet/i);
      expect(text).toMatch(/nothing can be entered or saved there until they launch/i);
      expect(text).not.toMatch(/does not save anything yet|try the alerts form|sign up for email/i);
      expect(screen.getByRole('link', { name: 'alerts page' })).toHaveAttribute('href', '/alerts');
    }
  );

  // The page reads server-only switches, so it must be rendered per request; a prerendered page would bake
  // in whatever the build saw.
  it('is rendered on every request', () => {
    expect(aboutDynamic).toBe('force-dynamic');
  });

  it.each([
    { alerts: 'true', catalogue: 'api' },
    { alerts: '', catalogue: 'fixture' },
  ])(
    'makes no compliance claims, counts or verification stamps (alerts "$alerts", catalogue "$catalogue")',
    async (env) => {
      const text = await aboutText(env);

      expect(text).not.toMatch(COMPLIANCE_CLAIMS);
      expect(text).not.toMatch(/\d|verified by|guaranteed/i);
    }
  );

  it('keeps the contact link in both alerts states', async () => {
    await aboutText({ alerts: 'true', catalogue: 'api' });
    expect(screen.getByRole('link', { name: 'contact page' })).toHaveAttribute('href', '/contact');
    cleanup();

    await aboutText({ alerts: '', catalogue: 'api' });
    expect(screen.getByRole('link', { name: 'contact page' })).toHaveAttribute('href', '/contact');
  });

  it('calls the site a design preview only while the listings are samples', async () => {
    const sample = await aboutText({ alerts: 'true', catalogue: 'fixture' });
    cleanup();
    const live = await aboutText({ alerts: 'true', catalogue: 'api' });

    expect(sample).toMatch(/design preview with sample listings/i);
    expect(live).not.toMatch(/design preview|sample listings/i);
    expect(live).toMatch(/offers change without notice/i);
  });
});

describe('draft status on every legal and editorial page', () => {
  it.each([...LEGAL_PAGES, { name: 'about', Page: AboutPage, metadata: aboutMetadata }])(
    '$name keeps the draft banner and "Not yet reviewed"',
    async ({ Page }) => {
      render(await Page());

      expect(screen.getByText(DRAFT_BANNER)).toBeInTheDocument();
      expect(screen.getByText(/last reviewed: not yet reviewed/i)).toBeInTheDocument();
    }
  );

  it('contact page keeps the draft banner and stays out of search engines', async () => {
    render(await ContactPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByText(DRAFT_BANNER)).toBeInTheDocument();
    expect(contactMetadata.robots).toEqual({ index: false });
  });

  it.each([...LEGAL_PAGES, { name: 'about', Page: AboutPage, metadata: aboutMetadata }])(
    '$name makes no compliance, registration or approval claims',
    async ({ Page }) => {
      const { container } = render(await Page());

      expect(container.textContent).not.toMatch(COMPLIANCE_CLAIMS);
    }
  );

  it('contact page makes no compliance claims and invents no operator details', async () => {
    const { container } = render(await ContactPage({ searchParams: Promise.resolve({}) }));

    expect(container.textContent).not.toMatch(COMPLIANCE_CLAIMS);
    expect(screen.getByText(/have not been added yet/i)).toBeInTheDocument();
  });
});

describe('privacy notice', () => {
  it('describes the alerts data, the double opt-in and the proposed retention, flagged for review', () => {
    const { container } = render(<PrivacyPage />);

    expect(container.textContent).toMatch(/IP address/);
    expect(container.textContent).toMatch(/error-monitoring provider/);
    expect(container.textContent).toMatch(/double opt-in/);
    expect(container.textContent).toMatch(/exact wording you saw/);
    expect(container.textContent).toMatch(/deleted after 30 days/);
    expect(container.textContent).toMatch(/12 months/);
    expect(container.textContent).toMatch(/for legal review/);
    expect(container.textContent).toMatch(/not .* running today/i);
    expect(container.textContent).not.toMatch(/carried to the alerts page/);
    expect(screen.getByRole('heading', { name: 'Email alerts' })).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/does not save any information|saves nothing/i);
  });

  it('says exactly where an address can and cannot appear in a page address (FREEJ-31)', () => {
    const { container } = render(<PrivacyPage />);
    const text = container.textContent ?? '';

    expect(text).toMatch(/never in the page address/);
    expect(text).toMatch(/single-use code in the page address/);
    expect(text).toMatch(/search words travel in the page address/i);
    expect(text).toMatch(/email address.*removed before an error or timing report/i);
    expect(text).not.toMatch(/never reaches (our )?(logs|error)/i);
  });

  it('describes the outbound click record as live behaviour, with its retention period', () => {
    const { container } = render(<PrivacyPage />);

    expect(screen.getByRole('heading', { name: 'If you use an offer button' })).toBeInTheDocument();
    expect(container.textContent).toMatch(
      /no IP address, no cookie identifier and no account identifier/i
    );
    expect(container.textContent).toMatch(/redirect sets no cookie/i);
    expect(container.textContent).toMatch(/deleted after 13 months \(395 days\)/i);
    expect(container.textContent).not.toMatch(
      /outbound-click counts are planned|we plan to count/i
    );
  });

  it('describes account data held when someone creates an account', () => {
    render(<PrivacyPage />);

    expect(screen.getByRole('heading', { name: 'If you create an account' })).toBeInTheDocument();
    expect(screen.getByText(/IP address and browser of each sign-in/i)).toBeInTheDocument();
  });
});

describe('cookie notice', () => {
  it('names the sign-in cookie and does not promise a banner is never needed', () => {
    const { container } = render(<CookiesPage />);

    expect(screen.getByRole('heading', { name: 'If you sign in' })).toBeInTheDocument();
    expect(container.textContent).toMatch(/called `session`/);
    expect(container.textContent).toMatch(/sign-up page prepares sign-in storage/);
    expect(container.textContent).toMatch(/still to be confirmed in review/i);
  });
});

describe('saved freebies in the legal drafts (FREEJ-28)', () => {
  it('the cookie notice names the storage key, says it is not a cookie, and stays a draft', () => {
    const { container } = render(<CookiesPage />);

    expect(screen.getByRole('heading', { name: 'If you save freebies' })).toBeInTheDocument();
    expect(container.textContent).toMatch(/freejoy\.saved\.v1/);
    expect(container.textContent).toMatch(/It is not a cookie/);
    expect(container.textContent).toMatch(/still to be confirmed in review/i);
    expect(container.textContent).toMatch(/draft/i);
  });

  it('the cookie notice says what the lookups leave behind, with the real periods, and shows the key as code', () => {
    const { container } = render(<CookiesPage />);
    const text = container.textContent ?? '';

    expect(text).toMatch(/not stored in a database or against an account/i);
    expect(text).toMatch(
      /page addresses of freebies you open can appear in error and timing reports/i
    );
    expect(text).toMatch(/14 days/);
    expect(text).toMatch(/30 days/);
    expect(text).not.toMatch(/short-lived/i);
    expect(text).not.toMatch(/name of a button/i);
    expect(container.querySelector('code')).toHaveTextContent('freejoy.saved.v1');
    expect(text).not.toContain('`freejoy.saved.v1`');
  });

  it('the privacy notice says the same, and asks the reviewer about backend and host log retention', () => {
    const { container } = render(<PrivacyPage />);
    const text = container.textContent ?? '';

    expect(text).toMatch(/list of freebies you saved stays in your browser/i);
    expect(text).toMatch(/no analytics event when you save or remove/i);
    expect(text).toMatch(/not stored in a database or against an account/i);
    expect(text).toMatch(
      /page addresses of freebies you open can appear in error and timing reports/i
    );
    expect(text).toMatch(/14 days/);
    expect(text).not.toMatch(/short-lived/i);
    expect(text).not.toMatch(/name of a button/i);
    expect(text).toMatch(/draft/i);
  });

  it('both drafts leave backend and host log retention to the reviewer', () => {
    for (const Page of [CookiesPage, PrivacyPage]) {
      const { container, unmount } = render(<Page />);

      expect(container.textContent).toMatch(
        /retention of the server logs of the backend and the hosting service/i
      );
      unmount();
    }
  });
});

describe('terms and about copy', () => {
  it('terms say competitions are run by the organiser, not Freejoy', () => {
    render(<TermsPage />);

    expect(screen.getByText(/competitions are run by the organiser/i)).toBeInTheDocument();
  });

  it('about explains that cashback and vouchers are not free and links the disclosure', async () => {
    render(await AboutPage());

    expect(screen.getByRole('heading', { name: 'Not everything is free' })).toBeInTheDocument();
    expect(screen.getByText(/cashback and vouchers are not free/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'affiliate disclosure' })).toHaveAttribute(
      'href',
      '/affiliate-disclosure'
    );
  });
});
