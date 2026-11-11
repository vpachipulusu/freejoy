/** the admin header keeps its menu and meets the 44px target rule. */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';

vi.mock('@/app/actions/auth-actions', () => ({ logoutAction: vi.fn() }));

import { AdminHeader } from '@/components/admin/admin-header';

describe('AdminHeader', () => {
  const user = { email: 'ada@example.com', displayName: 'Ada', avatarUrl: null };

  it('shows the role label with the underscore removed', () => {
    render(<AdminHeader user={user} adminRole="super_admin" />);
    expect(screen.getByText('super admin')).toBeInTheDocument();
  });

  it('has a 44px account menu trigger that opens Back to Dashboard and Logout', async () => {
    render(<AdminHeader user={user} adminRole="admin" />);
    const trigger = screen.getByRole('button', { name: /account menu/i });
    expect(trigger.className).toContain('h-11');
    expect(trigger.className).toContain('w-11');
    await setupUser().click(trigger);
    expect(await screen.findByRole('menuitem', { name: /back to dashboard/i })).toBeVisible();
    expect(screen.getByRole('menuitem', { name: /logout/i })).toBeVisible();
  });
});
