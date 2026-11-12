'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { announceNavigation } from '@/components/site/navigation-events';
import {
  changePasswordAction,
  deleteAccountAction,
  disableTwoFactorAction,
  exportMyDataAction,
  getLoginHistoryAction,
  getTwoFactorStatusAction,
} from '@/app/actions/user-actions';
import { Button } from '@/components/ui/button';
import { Panel } from '@/components/dashboard/overview/panel';
import { PageHeader, PanelHint } from '@/components/dashboard/settings/form-page';
import { StatusPill } from '@/components/ui/status-pill';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Loader2, X } from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import { ReauthDialog } from '@/components/auth/reauth-dialog';
import { TwoFactorCodeField } from '@/components/auth/two-factor-code-field';
import { reauthenticate, restoreSessionWithNewPassword } from '@/lib/firebase/reauth';
import { setSessionRefreshPaused } from '@/lib/firebase/session-refresh-pause';
import { auth } from '@/lib/firebase/client';
import { signOut } from 'firebase/auth';

const REVOKE_URL_DELAY_MS = 10_000;

interface LoginHistoryEntry {
  id: string;
  loginAt: string;
  ipAddress?: string | null;
  location?: string | null;
  successful: boolean;
}

const MISMATCH_ERROR = 'Passwords do not match';

export default function SecurityPage() {
  const { toast } = useToast();
  const router = useRouter();
  const [isLoading, setIsLoading] = useState(true);
  const [loginHistory, setLoginHistory] = useState<LoginHistoryEntry[]>([]);
  const [mismatchError, setMismatchError] = useState<string | null>(null);
  // null until the status loads, or when it could not be read (nothing is claimed in that case).
  const [twoFactorEnabled, setTwoFactorEnabled] = useState<boolean | null>(null);
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [isChangingPassword, setIsChangingPassword] = useState(false);

  const [showDisableTwoFactor, setShowDisableTwoFactor] = useState(false);
  const [twoFactorCode, setTwoFactorCode] = useState('');
  // True when the last disable attempt was refused by the API, as opposed to a wrong-password error from re-authentication.
  const [codeError, setCodeError] = useState(false);
  const [showDeleteAccount, setShowDeleteAccount] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [deleteConfirmation, setDeleteConfirmation] = useState('');

  const [passwordData, setPasswordData] = useState({
    currentPassword: '',
    newPassword: '',
    confirmPassword: '',
  });

  useEffect(() => {
    let cancelled = false;

    async function loadTwoFactorStatus() {
      try {
        const result = await getTwoFactorStatusAction();
        if (!cancelled && result.success && result.data) {
          setTwoFactorEnabled(result.data.enabled);
        }
      } catch (error) {
        console.error('Load two-factor status error:', error);
      }
    }

    async function loadLoginHistory() {
      try {
        const result = await getLoginHistoryAction();
        if (!cancelled && result.success && result.data) {
          setLoginHistory(result.data.history || []);
        }
      } catch (error) {
        console.error('Load login history error:', error);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    loadTwoFactorStatus();
    loadLoginHistory();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleChangePassword() {
    if (passwordData.newPassword !== passwordData.confirmPassword) {
      setMismatchError(MISMATCH_ERROR);
      return;
    }
    setMismatchError(null);

    try {
      setIsChangingPassword(true);
      // The re-authentication and re-sign-in below are ID token changes; keep the background session refresh
      // from racing the revoked cookie.
      setSessionRefreshPaused(true);

      // Re-authenticate with the CURRENT password; the backend only accepts an ID token minted in the last
      // five minutes through the password provider.
      const fresh = await reauthenticate('password', passwordData.currentPassword);
      if (fresh.error !== null) {
        toast({ title: 'Error', description: fresh.error, variant: 'destructive' });
        return;
      }

      const result = await changePasswordAction(fresh.idToken, passwordData.newPassword);

      if (!result.success) {
        toast({
          title: 'Error',
          description: result.error || 'Failed to change password',
          variant: 'destructive',
        });
        return;
      }

      // The change revoked this browser's refresh tokens and session cookie. Sign in again with the NEW
      // password to get a fresh ID token, then mint a new session cookie from it.
      const restored = await restoreSessionWithNewPassword(passwordData.newPassword);
      if (!restored) {
        toast({
          title: 'Password changed',
          description: 'Please sign in again with your new password.',
        });
        await signOut(auth).catch(() => undefined);
        announceNavigation('/login');
        router.push('/login');
        return;
      }

      toast({
        title: 'Success',
        description: 'Password changed successfully',
      });

      setShowChangePassword(false);
      setPasswordData({
        currentPassword: '',
        newPassword: '',
        confirmPassword: '',
      });
    } catch (error) {
      console.error('Change password error:', error);
      toast({
        title: 'Error',
        description: 'An unexpected error occurred',
        variant: 'destructive',
      });
    } finally {
      setSessionRefreshPaused(false);
      setIsChangingPassword(false);
    }
  }

  // Closing by Escape, the overlay or Cancel all land here, so typed passwords never outlive the dialog.
  function handleChangePasswordOpenChange(open: boolean) {
    setShowChangePassword(open);
    if (!open) {
      setMismatchError(null);
      setPasswordData({ currentPassword: '', newPassword: '', confirmPassword: '' });
    }
  }

  async function handleDisableTwoFactor(idToken: string): Promise<string | null> {
    setCodeError(false);
    const result = await disableTwoFactorAction(idToken, twoFactorCode);
    if (!result.success) {
      // Reaching the API means the password was accepted, so this failure is about the code.
      setCodeError(true);
      return result.error || 'Failed to disable two-factor authentication';
    }
    setTwoFactorEnabled(false);
    setTwoFactorCode('');
    setShowDisableTwoFactor(false);
    toast({ title: 'Success', description: 'Two-factor authentication disabled' });
    return null;
  }

  async function handleExport() {
    setIsExporting(true);
    try {
      const result = await exportMyDataAction();
      if (!result.success || !result.data) {
        toast({
          title: 'Export failed',
          description: result.error || 'Failed to export your data',
          variant: 'destructive',
        });
        return;
      }
      const url = URL.createObjectURL(new Blob([result.data.json], { type: 'application/json' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = result.data.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      // Revoking right after click() can cancel the download in some browsers.
      setTimeout(() => URL.revokeObjectURL(url), REVOKE_URL_DELAY_MS);
      toast({
        title: 'Export ready',
        description: result.data.truncated
          ? 'Your data download has started. Older rows were left out because the export keeps only the newest entries of each list.'
          : 'Your data download has started.',
      });
    } catch {
      toast({
        title: 'Export failed',
        description: 'Failed to export your data',
        variant: 'destructive',
      });
    } finally {
      setIsExporting(false);
    }
  }

  async function handleDeleteAccount(idToken: string): Promise<string | null> {
    const result = await deleteAccountAction(idToken);
    if (!result.success) return result.error || 'Failed to delete account';
    await signOut(auth).catch(() => undefined);
    announceNavigation('/');
    router.push('/');
    return null;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Security"
        description="Manage your account security and privacy settings"
      />

      <Panel title="Password" titleId="security-password">
        <PanelHint>Update your password to keep your account secure</PanelHint>
        <Dialog open={showChangePassword} onOpenChange={handleChangePasswordOpenChange}>
          <DialogTrigger asChild>
            <Button variant="accent">Change Password</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Change Password</DialogTitle>
              <DialogDescription>
                Enter your current password and choose a new one
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div>
                <Label htmlFor="current-password">Current Password</Label>
                <Input
                  id="current-password"
                  type="password"
                  autoComplete="current-password"
                  value={passwordData.currentPassword}
                  onChange={(e) =>
                    setPasswordData({ ...passwordData, currentPassword: e.target.value })
                  }
                />
              </div>
              <div>
                <Label htmlFor="new-password">New Password</Label>
                <Input
                  id="new-password"
                  type="password"
                  autoComplete="new-password"
                  value={passwordData.newPassword}
                  onChange={(e) => {
                    setMismatchError(null);
                    setPasswordData({ ...passwordData, newPassword: e.target.value });
                  }}
                />
              </div>
              <div>
                <Label htmlFor="confirm-password">Confirm New Password</Label>
                <Input
                  id="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  aria-invalid={mismatchError ? true : undefined}
                  aria-describedby={mismatchError ? 'confirm-password-error' : undefined}
                  value={passwordData.confirmPassword}
                  onChange={(e) => {
                    setMismatchError(null);
                    setPasswordData({ ...passwordData, confirmPassword: e.target.value });
                  }}
                />
                {mismatchError && (
                  <p
                    id="confirm-password-error"
                    role="alert"
                    className="mt-1.5 text-sm text-destructive"
                  >
                    {mismatchError}
                  </p>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => handleChangePasswordOpenChange(false)}
                disabled={isChangingPassword}
              >
                Cancel
              </Button>
              <Button onClick={handleChangePassword} disabled={isChangingPassword}>
                {isChangingPassword && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Change Password
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </Panel>

      {/* Two-factor status. Setup stays hidden until a recovery path exists (sign-in asks for the code, but there are no backup codes); disable is offered when it is on. */}
      <Panel title="Two-Factor Authentication" titleId="security-two-factor">
        <PanelHint>An extra verification step when you sign in</PanelHint>
        {twoFactorEnabled === null ? (
          <p className="text-sm text-muted-foreground">Two-factor status unavailable</p>
        ) : (
          <p className="text-sm" data-testid="two-factor-status">
            {twoFactorEnabled
              ? 'Two-factor authentication is enabled'
              : 'Two-factor authentication is not enabled'}
          </p>
        )}
        {twoFactorEnabled === true && (
          <Button variant="outline" className="mt-4" onClick={() => setShowDisableTwoFactor(true)}>
            Disable 2FA
          </Button>
        )}
      </Panel>

      <Panel title="Login History" titleId="security-login-history" busy={isLoading}>
        <PanelHint>Review recent activity on your account</PanelHint>
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2
              aria-hidden="true"
              className="h-8 w-8 text-muted-foreground motion-safe:animate-spin"
            />
            <span className="sr-only" role="status">
              Loading login history
            </span>
          </div>
        ) : loginHistory.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date & Time</TableHead>
                <TableHead>IP Address</TableHead>
                <TableHead>Location</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loginHistory.map((login) => (
                <TableRow key={login.id}>
                  <TableCell>{new Date(login.loginAt).toLocaleString()}</TableCell>
                  <TableCell className="font-mono text-sm">
                    {login.ipAddress || 'Unknown'}
                  </TableCell>
                  <TableCell>{login.location || 'Unknown'}</TableCell>
                  <TableCell>
                    {login.successful ? (
                      <StatusPill variant="good">Success</StatusPill>
                    ) : (
                      <StatusPill variant="crit" icon={X}>
                        Failed
                      </StatusPill>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No login history available
          </p>
        )}
      </Panel>

      <Panel title="Danger Zone" titleId="security-danger-zone" className="border-destructive/50">
        <PanelHint>Permanently delete your account and all of its data</PanelHint>
        <Button variant="destructive" onClick={() => setShowDeleteAccount(true)}>
          Delete Account
        </Button>
      </Panel>

      <ReauthDialog
        open={showDisableTwoFactor}
        onOpenChange={(open) => {
          setShowDisableTwoFactor(open);
          if (!open) {
            setTwoFactorCode('');
            setCodeError(false);
          }
        }}
        title="Disable Two-Factor Authentication"
        description="Confirm your password and enter a code from your authenticator app"
        confirmLabel="Disable 2FA"
        destructive
        canConfirm={/^\d{6}$/.test(twoFactorCode)}
        onConfirm={handleDisableTwoFactor}
      >
        {({ error, errorId }) => (
          <TwoFactorCodeField
            id="disable-2fa-code"
            label="Authenticator code"
            value={twoFactorCode}
            onChange={(value) => {
              setCodeError(false);
              setTwoFactorCode(value);
            }}
            describedBy={error && codeError ? errorId : undefined}
          />
        )}
      </ReauthDialog>

      <ReauthDialog
        open={showDeleteAccount}
        onOpenChange={(open) => {
          setShowDeleteAccount(open);
          if (!open) setDeleteConfirmation('');
        }}
        title="Delete Account"
        description="This action cannot be undone. Your account and its data will be permanently deleted."
        confirmLabel="Delete Account"
        destructive
        canConfirm={deleteConfirmation === 'DELETE'}
        onConfirm={handleDeleteAccount}
      >
        <div>
          <Label htmlFor="delete-confirmation">Type DELETE to confirm</Label>
          <Input
            id="delete-confirmation"
            placeholder="DELETE"
            value={deleteConfirmation}
            onChange={(e) => setDeleteConfirmation(e.target.value)}
          />
        </div>
      </ReauthDialog>

      <Panel title="Your data" titleId="security-data-export">
        <PanelHint>
          Download your profile, settings, organisation memberships, login history and notifications
          as a JSON file. Exports are limited to a few per hour and recorded in the audit log.
        </PanelHint>
        <Button
          variant="outline"
          onClick={handleExport}
          disabled={isExporting}
          aria-busy={isExporting}
        >
          {isExporting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Export my data
        </Button>
      </Panel>
    </div>
  );
}
