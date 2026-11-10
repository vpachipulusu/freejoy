import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Send } from 'lucide-react';
import { DraftBanner } from '@/components/legal/legal-page';
import Link from 'next/link';

type FieldProps = React.ComponentProps<typeof Input> & {
  id: string;
  label: string;
  hint: string;
  required?: boolean;
};

/** Plan 5.8 field: label (required marked in text), 44px control, hint linked by aria-describedby. */
function Field({ id, label, hint, required, ...inputProps }: FieldProps) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{`${label} (${required ? 'required' : 'optional'})`}</Label>
      <Input id={id} required={required} aria-describedby={`${id}-hint`} {...inputProps} />
      <p id={`${id}-hint`} className="text-sm text-muted-foreground">
        {hint}
      </p>
    </div>
  );
}

export const metadata = {
  title: 'Contact Us',
  description: 'Contact details for Freejoy are coming soon. Draft.',
  robots: { index: false },
};

export default async function ContactPage({
  searchParams,
}: {
  searchParams: Promise<{ plan?: string }>;
}) {
  const params = await searchParams;
  // ?plan=a&plan=b arrives as an array at runtime
  const selectedPlan = typeof params.plan === 'string' ? params.plan : undefined;
  const planLabel = selectedPlan && selectedPlan.charAt(0).toUpperCase() + selectedPlan.slice(1);

  return (
    <div className="mx-auto max-w-content px-4 py-6 sm:px-6 lg:py-8">
      <div className="mx-auto max-w-5xl">
        <DraftBanner />
        {/* Header */}
        <div className="mb-8 mt-6 rounded-editorial bg-secondary px-6 py-10 text-center sm:px-10 sm:py-12">
          <h1 className="mb-4 font-display text-4xl font-extrabold tracking-tight sm:text-5xl">
            Contact Us
          </h1>
          <p className="mx-auto max-w-2xl text-lg text-muted-foreground sm:text-xl">
            A support contact is coming soon. Messages cannot be sent from this page yet.
          </p>
          {selectedPlan && (
            <p className="mt-4 text-sm text-accent-ink">Interested in the {planLabel} plan</p>
          )}
        </div>

        <div className="grid gap-8 md:grid-cols-3">
          {/* Contact Form */}
          <div className="md:col-span-2">
            <Card className="rounded-card">
              <CardHeader>
                <CardTitle>Send us a message</CardTitle>
                <CardDescription>
                  This form is unavailable and does not send messages.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <fieldset disabled aria-label="Contact form unavailable" className="space-y-6">
                  <div className="grid gap-4 md:grid-cols-2">
                    <Field id="firstName" label="First name" required hint="Your given name." />
                    <Field id="lastName" label="Last name" required hint="Your family name." />
                  </div>

                  <Field
                    id="email"
                    label="Email"
                    type="email"
                    required
                    hint="We would reply to this address."
                  />

                  <Field id="company" label="Company" hint="Leave blank if not applicable." />

                  {selectedPlan && (
                    <Field
                      id="plan"
                      label="Interested plan"
                      hint="Chosen on the pricing page."
                      defaultValue={planLabel}
                      readOnly
                      className="bg-muted"
                    />
                  )}

                  <Field
                    id="subject"
                    label="Subject"
                    required
                    hint="A short summary of your request."
                  />

                  <div className="space-y-2">
                    <Label htmlFor="message">Message (required)</Label>
                    <Textarea id="message" rows={6} required aria-describedby="message-hint" />
                    <p id="message-hint" className="text-sm text-muted-foreground">
                      Tell us what you need.
                    </p>
                  </div>

                  <Button
                    type="button"
                    disabled
                    size="lg"
                    variant="accent"
                    className="min-h-12 w-full rounded-pill font-bold"
                  >
                    <Send className="mr-2 h-4 w-4" />
                    Send message
                  </Button>

                  <p className="text-center text-sm text-muted-foreground">
                    Message sending is coming soon.
                  </p>
                </fieldset>
              </CardContent>
            </Card>
          </div>

          {/* Support availability */}
          <div className="space-y-6">
            <Card className="rounded-card bg-good text-good-foreground">
              <CardHeader>
                <CardTitle className="text-lg">Support Contact</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">
                  A support contact is coming soon. Email, phone and sales channels are not
                  available yet.
                </p>
              </CardContent>
            </Card>
            <Card className="rounded-card">
              <CardHeader>
                <CardTitle className="text-lg">Who runs Freejoy</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">
                  The operator&apos;s name, address and contact email will be shown here before any
                  real offer goes live. They have not been added yet.
                </p>
              </CardContent>
            </Card>
            <Card className="rounded-card">
              <CardHeader>
                <CardTitle className="text-lg">Retailers and brands</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">
                  To have a free sample, trial or similar offer listed, read{' '}
                  <Link href="/partners" className="font-semibold text-accent-ink underline">
                    List your free sample
                  </Link>
                  .
                </p>
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}
