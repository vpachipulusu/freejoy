import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { OfferImportBadge } from '@/app/(admin)/admin/offers/_components/offer-import-badge';
import { toOfferRow } from '@/lib/api/admin-offers';

describe('the offers list badge for a retailer submission', () => {
  it('says "Submitted by retailer" with the ref', () => {
    render(<OfferImportBadge importProvider="partner" sourceChangedAt={null} partnerRef="sku-1" />);

    expect(screen.getByTestId('offer-import-badge')).toHaveTextContent(
      'Submitted by retailer · ref sku-1'
    );
  });

  it('says an update is waiting when the marker is set', () => {
    render(
      <OfferImportBadge
        importProvider="partner"
        sourceChangedAt="2026-02-05T00:00:00Z"
        partnerRef="sku-1"
      />
    );

    expect(screen.getByTestId('offer-import-badge')).toHaveTextContent('Retailer update waiting');
  });

  it('works without a ref and leaves the Awin badges alone', () => {
    const { rerender } = render(
      <OfferImportBadge importProvider="partner" sourceChangedAt={null} />
    );
    expect(screen.getByTestId('offer-import-badge')).toHaveTextContent(/^Submitted by retailer$/);

    rerender(<OfferImportBadge importProvider="awin" sourceChangedAt={null} />);
    expect(screen.getByTestId('offer-import-badge')).toHaveTextContent('Imported');
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
