/** The edit merchant page shows the partner keys section for the merchant it loaded, under the form. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const mocks = vi.hoisted(() => ({
  requireAdminPage: vi.fn(),
  fetchAdminMerchant: vi.fn(),
  section: vi.fn(),
}));

vi.mock('@/app/(admin)/admin/_lib/require-admin-page', () => ({
  requireAdminPage: mocks.requireAdminPage,
}));
vi.mock('@/app/(admin)/admin/merchants/merchant-form', () => ({
  MerchantForm: () => <div>merchant form</div>,
}));
vi.mock('@/app/(admin)/admin/merchants/_components/partner-keys-section', () => ({
  PartnerKeysSection: (props: unknown) => {
    mocks.section(props);
    return <h2>Partner API keys</h2>;
  },
}));
vi.mock('@/lib/logger', () => ({ logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }));
vi.mock('@/lib/api/admin-merchants', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/admin-merchants')>(
    '@/lib/api/admin-merchants'
  );
  return { ...actual, fetchAdminMerchant: mocks.fetchAdminMerchant };
});

import EditMerchantPage from '@/app/(admin)/admin/merchants/[id]/page';

beforeEach(() => {
  vi.clearAllMocks();
  mocks.requireAdminPage.mockResolvedValue(undefined);
  mocks.fetchAdminMerchant.mockResolvedValue({
    success: true,
    data: {
      id: 'm1',
      name: 'Acme',
      slug: 'acme',
      website: 'https://shop.example.com',
      logo_url: null,
      active: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-02T00:00:00Z',
    },
  });
});

describe('the edit merchant page', () => {
  it('shows the partner keys of the merchant under the form', async () => {
    render(await EditMerchantPage({ params: Promise.resolve({ id: 'm1' }) }));

    expect(screen.getByText('merchant form')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Partner API keys' })).toBeInTheDocument();
    expect(mocks.section).toHaveBeenCalledWith({
      merchantId: 'm1',
      merchantName: 'Acme',
      website: 'https://shop.example.com',
      active: true,
    });
  });
});
