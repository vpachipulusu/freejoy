'use client';

import { useRef, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import { useRouter } from 'next/navigation';
import { announceNavigation } from '@/components/site/navigation-events';
import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { signUpWithEmail } from '@/lib/firebase/auth-helpers';
import { verifyTokenAction } from '@/app/actions/auth-actions';
import { AuthFormAlert, FIELD_ERROR_CLASS } from './auth-form-alert';
import {
  classifyAuthError,
  classifyAuthResult,
  classifyThrownAuthError,
  REDIRECT_RECOVERY_MS,
  type AuthFormError,
} from './auth-form-errors';

const signupSchema = z
  .object({
    name: z.string().min(2, 'Name must be at least 2 characters'),
    email: z.string().email('Please enter a valid email address'),
    password: z
      .string()
      .min(8, 'Password must be at least 8 characters')
      .regex(
        /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)/,
        'Password must contain at least one uppercase letter, one lowercase letter, and one number'
      ),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords don't match",
    path: ['confirmPassword'],
  });

type SignupFormValues = z.infer<typeof signupSchema>;

interface SignupFormProps {
  selectedPlan?: string;
}

export function SignupForm({ selectedPlan }: SignupFormProps) {
  const router = useRouter();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<AuthFormError | null>(null);
  const isSubmitting = useRef(false);

  const form = useForm<SignupFormValues>({
    resolver: zodResolver(signupSchema),
    defaultValues: {
      name: '',
      email: '',
      password: '',
      confirmPassword: '',
    },
  });

  function release() {
    isSubmitting.current = false;
    setIsLoading(false);
  }

  async function onSubmit(data: SignupFormValues) {
    if (isSubmitting.current) return;
    isSubmitting.current = true;
    let redirected = false;
    try {
      setIsLoading(true);
      setError(null);

      const {
        user,
        idToken,
        error: signUpError,
      } = await signUpWithEmail(data.email, data.password);

      if (signUpError) {
        setError(classifyAuthError(signUpError, 'sign-up'));
        return;
      }

      if (user && idToken) {
        // Create session on server. The selected plan never sets the tier; paid plans go through checkout.
        const result = await verifyTokenAction(idToken);

        if (!result.success) {
          setError(classifyAuthResult(result, 'sign-up'));
          return;
        }

        // Redirect to verify email page or dashboard
        redirected = true;
        // If navigation fails or stalls, give the form back instead of leaving it locked.
        setTimeout(release, REDIRECT_RECOVERY_MS);
        announceNavigation('/verify-email');
        router.push('/verify-email');
      }
    } catch (error) {
      redirected = false;
      setError(classifyThrownAuthError(error, 'sign-up'));
      console.error('Signup error:', error);
    } finally {
      // Stay locked while the redirect is in flight so a second submit cannot run.
      if (!redirected) release();
    }
  }

  return (
    <Form {...form}>
      <form
        onSubmit={(event) => form.handleSubmit(onSubmit)(event)}
        className="space-y-4"
        noValidate
        aria-busy={isLoading}
      >
        {error && <AuthFormAlert error={error} />}

        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Full Name</FormLabel>
              <FormControl>
                <Input
                  placeholder="John Doe"
                  type="text"
                  autoComplete="name"
                  disabled={isLoading}
                  className={FIELD_ERROR_CLASS}
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input
                  placeholder="name@example.com"
                  type="email"
                  autoComplete="email"
                  disabled={isLoading}
                  className={FIELD_ERROR_CLASS}
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Password</FormLabel>
              <FormControl>
                <Input
                  placeholder="Create a strong password"
                  type="password"
                  autoComplete="new-password"
                  disabled={isLoading}
                  className={FIELD_ERROR_CLASS}
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="confirmPassword"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Confirm Password</FormLabel>
              <FormControl>
                <Input
                  placeholder="Confirm your password"
                  type="password"
                  autoComplete="new-password"
                  disabled={isLoading}
                  className={FIELD_ERROR_CLASS}
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <Button type="submit" variant="accent" className="w-full" disabled={isLoading}>
          {isLoading ? (
            <>
              <div
                aria-hidden="true"
                className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
              />
              Creating account...
            </>
          ) : (
            'Create Account'
          )}
        </Button>
      </form>
    </Form>
  );
}
