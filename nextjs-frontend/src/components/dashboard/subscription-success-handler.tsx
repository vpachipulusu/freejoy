'use client';

import { useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { announceNavigation } from '@/components/site/navigation-events';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { CheckCircle2, Clock, Loader2 } from 'lucide-react';
import {
  getSubscriptionStatusAction,
  verifyUpgradeSessionAction,
} from '@/app/actions/subscription-actions';

const POLL_INTERVAL_MS = 3000;
const MAX_POLLS = 10; // About 30 seconds after the first check
const ACTIVATED_REDIRECT_MS = 2000;

export function SubscriptionSuccessHandler() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const upgradeSuccess = searchParams.get('upgrade') === 'success';

  const [subscriptionUpdated, setSubscriptionUpdated] = useState(false);
  const [isVerifying, setIsVerifying] = useState(upgradeSuccess);
  const [isValidSession, setIsValidSession] = useState(false);
  const [timedOut, setTimedOut] = useState(false);

  // Verify checkout session first
  useEffect(() => {
    if (!upgradeSuccess) return;

    const verifySession = async () => {
      setIsVerifying(true);
      try {
        const result = await verifyUpgradeSessionAction();

        if (result.success && result.data?.valid) {
          // Valid checkout session - proceed with polling
          setIsValidSession(true);
        } else {
          // Invalid or fake session - remove query param immediately
          console.warn('Invalid checkout session:', result.data?.reason);
          announceNavigation('/dashboard');
          router.replace('/dashboard');
        }
      } catch (error) {
        console.error('Error verifying checkout session:', error);
        announceNavigation('/dashboard');
        router.replace('/dashboard');
      } finally {
        setIsVerifying(false);
      }
    };

    verifySession();
  }, [upgradeSuccess, router]);

  // Poll for the plan to change, only for a verified checkout. The count lives in the effect, not in state.
  useEffect(() => {
    if (!isValidSession || subscriptionUpdated) return;

    let cancelled = false;
    let inFlight = false;
    let polls = 0;
    let pollInterval: ReturnType<typeof setInterval> | undefined;

    const checkSubscription = async (): Promise<boolean> => {
      try {
        const result = await getSubscriptionStatusAction();
        if (cancelled) return false;

        if (result.success && result.data) {
          const { subscriptionTier, subscriptionStatus } = result.data;

          // Check if subscription has been updated to pro or enterprise
          if (subscriptionTier !== 'free' && subscriptionStatus === 'active') {
            setSubscriptionUpdated(true);
            return true;
          }
        }
        return false;
      } catch (error) {
        console.error('Error checking subscription:', error);
        return false;
      }
    };

    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      polls += 1;
      const updated = await checkSubscription();
      inFlight = false;

      if (cancelled || updated) {
        clearInterval(pollInterval);
        return;
      }
      if (polls >= MAX_POLLS) {
        clearInterval(pollInterval);
        // One last check, so a webhook that landed during the final tick never leaves the "not updated" banner
        // beside a Pro plan.
        if (await checkSubscription()) return;
        if (cancelled) return;
        setTimedOut(true);
        // Keep ?upgrade=success so the banner below stays visible: the payment went through but the plan has
        // not changed yet (the Stripe webhook can be late or failing), and the user needs to be told.
        router.refresh();
      }
    };

    // Initial check immediately, then every few seconds
    checkSubscription().then((updated) => {
      if (cancelled || updated) return;
      pollInterval = setInterval(poll, POLL_INTERVAL_MS);
    });

    return () => {
      cancelled = true;
      clearInterval(pollInterval);
    };
  }, [isValidSession, subscriptionUpdated, router]);

  // Once the plan is active, show the success message briefly, then drop the query parameter and refresh
  useEffect(() => {
    if (!subscriptionUpdated) return;

    const redirect = setTimeout(() => {
      announceNavigation('/dashboard');
      router.replace('/dashboard');
      router.refresh();
    }, ACTIVATED_REDIRECT_MS);

    return () => clearTimeout(redirect);
  }, [subscriptionUpdated, router]);

  // Don't show anything if not upgrade success or still verifying
  if (!upgradeSuccess || (isVerifying && !isValidSession)) return null;

  if (timedOut && !subscriptionUpdated) {
    return (
      <Alert className="border-amber-500 bg-amber-50 dark:bg-amber-950">
        <div className="flex items-start gap-3">
          <Clock className="h-5 w-5 text-amber-600 dark:text-amber-400" aria-hidden="true" />
          <div className="flex-1">
            <AlertTitle className="text-amber-900 dark:text-amber-100">Payment received</AlertTitle>
            <AlertDescription className="text-amber-800 dark:text-amber-200">
              Your payment went through, but your plan has not updated yet. Refresh this page in a
              few minutes to check. If it still shows Free after that, please contact support.
            </AlertDescription>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => {
                announceNavigation('/dashboard');
                router.replace('/dashboard');
              }}
            >
              Dismiss
            </Button>
          </div>
        </div>
      </Alert>
    );
  }

  return (
    <Alert className="border-green-500 bg-green-50 dark:bg-green-950">
      <div className="flex items-start gap-3">
        {subscriptionUpdated ? (
          <CheckCircle2 className="h-5 w-5 text-green-600 dark:text-green-400" aria-hidden="true" />
        ) : (
          <Loader2
            className="h-5 w-5 animate-spin text-green-600 dark:text-green-400"
            aria-hidden="true"
          />
        )}
        <div className="flex-1">
          <AlertTitle className="text-green-900 dark:text-green-100">
            {subscriptionUpdated ? 'Subscription Activated!' : 'Processing Your Subscription...'}
          </AlertTitle>
          <AlertDescription className="text-green-800 dark:text-green-200">
            {subscriptionUpdated
              ? 'Your subscription has been successfully activated. Refreshing your dashboard...'
              : 'Please wait while we confirm your payment and activate your subscription. This usually takes just a few seconds.'}
          </AlertDescription>
        </div>
      </div>
    </Alert>
  );
}
