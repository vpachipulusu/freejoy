import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { OfferImportBadge } from '@/app/(admin)/admin/offers/_components/offer-import-badge';
import { toOfferRow } from '@/lib/api/admin-offers';

describe('the offers list badge for a retailer submission', () => {
  it('says only "Submitted by retailer" in the pill, with the ref in an element of its own', () => {
    render(<OfferImportBadge importProvider="partner" sourceChangedAt={null} partnerRef="sku-1" />);

    const pill = screen.getByTestId('offer-import-badge');
    expect(pill).toHaveTextContent(/^Submitted by retailer$/);
    const ref = screen.getByTestId('offer-partner-ref');
    expect(ref).toHaveTextContent('Ref sku-1');
    expect(pill).not.toContainElement(ref);
    expect(ref).toHaveClass('text-muted-foreground', '[overflow-wrap:anywhere]');
  });

  it('says only "Retailer update waiting" in the pill when the marker is set', () => {
    render(
      <OfferImportBadge
        importProvider="partner"
        sourceChangedAt="2026-02-05T00:00:00Z"
        partnerRef="sku-1"
      />
    );

    expect(screen.getByTestId('offer-import-badge')).toHaveTextContent(/^Retailer update waiting$/);
    expect(screen.getByTestId('offer-partner-ref')).toHaveTextContent('Ref sku-1');
  });

  it('keeps a 64-character ref out of the pill, which cannot wrap', () => {
    const ref64 = 'r'.repeat(64);
    render(<OfferImportBadge importProvider="partner" sourceChangedAt={null} partnerRef={ref64} />);

    expect(screen.getByTestId('offer-import-badge')).not.toHaveTextContent(ref64);
    expect(screen.getByTestId('offer-partner-ref')).toHaveTextContent(ref64);
  });

  it('works without a ref and leaves the Awin badges alone', () => {
    const { rerender } = render(
      <OfferImportBadge importProvider="partner" sourceChangedAt={null} />
    );
    expect(screen.getByTestId('offer-import-badge')).toHaveTextContent(/^Submitted by retailer$/);
    expect(screen.queryByTestId('offer-partner-ref')).not.toBeInTheDocument();

    rerender(<OfferImportBadge importProvider="awin" sourceChangedAt={null} />);
    expect(screen.getByTestId('offer-import-badge')).toHaveTextContent('Imported');
    expect(screen.queryByTestId('offer-partner-ref')).not.toBeInTheDocument();

    rerender(<OfferImportBadge importProvider="awin" sourceChangedAt="2026-02-05T00:00:00Z" />);
    expect(screen.getByTestId('offer-import-badge')).toHaveTextContent('Source changed');
  });

  it('maps the ref of a list row', () => {
    const row = toOfferRow({
      id: 'o1',
      title: 't',
      slug: 's',
      category_id: 'c',
      merchant_id: 'm',
      offer_type: 'FREE_SAMPLE',
      commercial_basis: 'EDITORIAL',
      status: 'DRAFT',
      featured: false,
      end_at: null,
      last_verified_at: null,
      updated_at: '2026-01-01T00:00:00Z',
      import_provider: 'partner',
      source_changed_at: null,
      partner_ref: 'sku-1',
    } as never);

    expect(row).toMatchObject({ importProvider: 'partner', partnerRef: 'sku-1' });
  });
});
