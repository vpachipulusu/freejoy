'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { announceNavigation } from '@/components/site/navigation-events';
import Link from 'next/link';
import { MailCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { auth } from '@/lib/firebase/client';
import { onAuthStateChanged, type User } from 'firebase/auth';
import { resendVerificationEmail } from '@/lib/firebase/auth-helpers';
import { refreshSessionAction } from '@/app/actions/auth-actions';
import { AUTH_LINK_CLASS, AuthHeading, AuthIconTile } from '../_components/auth-shared';

export default function VerifyEmailPage() {
  const router = useRouter();
  const [isResending, setIsResending] = useState(false);
  const [resendSuccess, setResendSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [userEmail, setUserEmail] = useState<string>('');

  const refreshSessionAndContinue = async (user: User) => {
    const idToken = await user.getIdToken(true);
    const result = await refreshSessionAction(idToken);
    if (result.success && result.data?.claimsEmailVerified === true) {
      announceNavigation('/dashboard');
      router.push('/dashboard');
    } else if (result.data?.signInRequired === true) {
      announceNavigation('/login');
      router.push('/login');
    } else {
      setError('Could not confirm your verified email yet. Please try again in a moment.');
    }
  };

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (user) => {
      if (!user) {
        // User not logged in, redirect to login
        announceNavigation('/login');
        router.push('/login');
        return;
      }

      setUserEmail(user.email || '');

      // Already verified in Firebase: refresh the server session first. The dashboard checks the signed
      // claim in the session cookie, so redirecting before it is re-minted would bounce straight back.
      if (user.emailVerified) {
        void refreshSessionAndContinue(user);
      }
    });

    return () => unsubscribe();
  }, [router]);

  const handleResendEmail = async () => {
    try {
      setIsResending(true);
      setError(null);
      setResendSuccess(false);

      const user = auth.currentUser;
      if (!user) {
        setError('No user found. Please log in again.');
        return;
      }

      const { error: resendError } = await resendVerificationEmail(user);

      if (resendError) {
        setError(resendError);
        return;
      }

      setResendSuccess(true);
    } catch (error) {
      setError('Failed to resend verification email. Please try again.');
      console.error('Resend email error:', error);
    } finally {
      setIsResending(false);
    }
  };

  const handleRefresh = async () => {
    const user = auth.currentUser;
    if (user) {
      await user.reload();
      if (user.emailVerified) {
        await refreshSessionAndContinue(user);
      } else {
        setError('Email not verified yet. Please check your inbox.');
      }
    }
  };

  return (
    <section className="space-y-6">
      <header className="space-y-2 text-center">
        <AuthIconTile>
          <MailCheck className="h-6 w-6" aria-hidden="true" />
        </AuthIconTile>
        <AuthHeading>Verify your email</AuthHeading>
        <p className="text-sm text-muted-foreground">
          We&apos;ve sent a verification link to{' '}
          <span className="font-medium text-foreground">{userEmail}</span>
        </p>
      </header>
      <div className="space-y-4">
        {error && (
          <div role="alert" className="rounded-md bg-crit p-3 text-sm text-crit-foreground">
            {error}
          </div>
        )}

        {resendSuccess && (
          <div role="status" className="rounded-md bg-good p-3 text-sm text-good-foreground">
            Verification email sent! Please check your inbox.
          </div>
        )}

        <div className="space-y-2 text-center text-sm text-muted-foreground">
          <p>Click the link in the email to verify your account.</p>
          <p>If you don&apos;t see the email, check your spam folder or request a new one.</p>
        </div>

        <div className="space-y-2">
          <Button
            onClick={handleResendEmail}
            variant="outline"
            size="lg"
            className="w-full"
            disabled={isResending}
          >
            {isResending ? (
              <>
                <div
                  aria-hidden="true"
                  className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent motion-reduce:animate-none"
                />
                Sending...
              </>
            ) : (
              'Resend verification email'
            )}
          </Button>

          <Button onClick={handleRefresh} variant="accent" size="lg" className="w-full">
            I&apos;ve verified my email
          </Button>
        </div>
      </div>
      <p className="text-center text-sm text-muted-foreground">
        Wrong email?{' '}
        <Link href="/signup" className={AUTH_LINK_CLASS}>
          Create a new account
        </Link>
      </p>
    </section>
  );
}
