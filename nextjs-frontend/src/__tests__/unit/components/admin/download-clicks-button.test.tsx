/**
 * "Download CSV": it calls the server action with the merchant and range, saves the returned file, announces
 * progress and the result in a status region, and shows a failure in an alert without ever saving a file.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { findIdleControl } from '@/__tests__/unit/helpers/idle';

const mocks = vi.hoisted(() => ({ exportMerchantClicksAction: vi.fn(), saveCsvFile: vi.fn() }));

vi.mock('@/app/actions/admin/merchant-clicks-actions', () => ({
  exportMerchantClicksAction: mocks.exportMerchantClicksAction,
}));
vi.mock('@/lib/admin-clicks/download', () => ({ saveCsvFile: mocks.saveCsvFile }));

import {
  DOWNLOAD_FAILED_MESSAGE,
  DownloadClicksButton,
  TRUNCATED_FILE_NOTE,
} from '@/app/(admin)/admin/analytics/download-clicks-button';

const PROPS = { merchantId: 'm1', from: '2026-03-10', to: '2026-03-31' };
const FILE = {
  filename: 'freejoy-clicks-acme-2026-03-10-to-2026-03-31.csv',
  csv: 'a,b',
  truncated: false,
};

describe('DownloadClicksButton', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it('is a real button with live regions already in the page', () => {
    render(<DownloadClicksButton {...PROPS} />);

    const button = screen.getByRole('button', { name: 'Download CSV' });
    expect(button).toHaveAttribute('type', 'button');
    expect(screen.getByRole('status')).toHaveTextContent('');
    expect(screen.getByRole('alert')).toHaveTextContent('');
  });

  it('asks the action for this merchant and range, saves the file and says so', async () => {
    const user = setupUser();
    mocks.exportMerchantClicksAction.mockResolvedValue({ success: true, data: FILE });
    render(<DownloadClicksButton {...PROPS} />);

    await user.click(screen.getByRole('button', { name: 'Download CSV' }));

    expect(mocks.exportMerchantClicksAction).toHaveBeenCalledWith(PROPS);
    expect(mocks.saveCsvFile).toHaveBeenCalledWith(FILE.filename, FILE.csv);
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(`Download started: ${FILE.filename}.`)
    );
    expect(screen.getByRole('alert')).toHaveTextContent('');
  });

  it('works from the keyboard', async () => {
    const user = setupUser();
    mocks.exportMerchantClicksAction.mockResolvedValue({ success: true, data: FILE });
    render(<DownloadClicksButton {...PROPS} />);

    screen.getByRole('button', { name: 'Download CSV' }).focus();
    await user.keyboard('{Enter}');

    await waitFor(() => expect(mocks.saveCsvFile).toHaveBeenCalledTimes(1));
  });

  it('stays focusable and says it is preparing while the file is built', async () => {
    const user = setupUser();
    let finish: (value: unknown) => void = () => undefined;
    mocks.exportMerchantClicksAction.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    render(<DownloadClicksButton {...PROPS} />);
    const button = screen.getByRole('button', { name: 'Download CSV' });

    await user.click(button);

    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).not.toBeDisabled();
    expect(button).toHaveFocus();
    expect(screen.getByRole('status')).toHaveTextContent('Preparing the file…');
    finish({ success: true, data: FILE });
    await waitFor(() => expect(button).not.toHaveAttribute('aria-disabled'));
  });

  it('starts one download however often Enter is pressed while it works, and keeps focus', async () => {
    const user = setupUser();
    let finish: (value: unknown) => void = () => undefined;
    mocks.exportMerchantClicksAction.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    render(<DownloadClicksButton {...PROPS} />);
    const button = screen.getByRole('button', { name: 'Download CSV' });

    button.focus();
    await user.keyboard('{Enter}');
    await user.keyboard('{Enter}');

    expect(mocks.exportMerchantClicksAction).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(button);
    finish({ success: true, data: FILE });
    await waitFor(() => expect(mocks.saveCsvFile).toHaveBeenCalledTimes(1));
  });

  it('says that the file lists only the offers shown when the report was cut', async () => {
    const user = setupUser();
    mocks.exportMerchantClicksAction.mockResolvedValue({
      success: true,
      data: { ...FILE, truncated: true },
    });
    render(<DownloadClicksButton {...PROPS} />);

    await user.click(screen.getByRole('button', { name: 'Download CSV' }));

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(TRUNCATED_FILE_NOTE));
  });

  it('shows the curated refusal in an alert and saves nothing', async () => {
    const user = setupUser();
    mocks.exportMerchantClicksAction.mockResolvedValue({
      success: false,
      error: 'You do not have permission to do that',
    });
    render(<DownloadClicksButton {...PROPS} />);

    await user.click(screen.getByRole('button', { name: 'Download CSV' }));

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('You do not have permission to do that')
    );
    expect(mocks.saveCsvFile).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Download CSV' })).not.toHaveAttribute(
        'aria-disabled'
      )
    );
  });

  it('shows a fixed message when the action throws', async () => {
    const user = setupUser();
    mocks.exportMerchantClicksAction.mockRejectedValue(new Error('SECRET network detail'));
    render(<DownloadClicksButton {...PROPS} />);

    await user.click(screen.getByRole('button', { name: 'Download CSV' }));

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(DOWNLOAD_FAILED_MESSAGE)
    );
    expect(document.body.textContent).not.toContain('SECRET');
  });

  it('clears an earlier failure when the next attempt works', async () => {
    const user = setupUser();
    mocks.exportMerchantClicksAction
      .mockResolvedValueOnce({ success: false, error: 'Try later' })
      .mockResolvedValueOnce({ success: true, data: FILE });
    render(<DownloadClicksButton {...PROPS} />);

    await user.click(screen.getByRole('button', { name: 'Download CSV' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Try later'));
    await user.click(await findIdleControl('button', 'Download CSV'));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(''));
    expect(mocks.saveCsvFile).toHaveBeenCalledTimes(1);
  });
});
