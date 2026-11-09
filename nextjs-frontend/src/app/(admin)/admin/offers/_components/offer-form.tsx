'use client';

import { useTransition, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { createOfferAction, updateOfferAction } from '@/app/actions/admin/offer-actions';
import { offerFormSchema, type OfferFormValues } from '@/lib/admin-catalogue/offer-schema';
import { settleAction } from '@/lib/admin-catalogue/settle-action';
import type { OfferRowStatus } from '@/lib/admin-catalogue/offer-status';
import { useToast } from '@/hooks/use-toast';
import { useReportFormBusy, useReportFormDirty } from '../../_components/dirty-form-context';
import { FormFooter } from '../../_components/form-footer';
import {
  useSavedMessage,
  useServerFormErrors,
  useSubmitOnce,
} from '../../_components/use-server-form-errors';
import { useUnsavedChanges } from '../../_components/use-unsaved-changes';
import {
  BasicsFields,
  ClassificationFields,
  PictureFields,
  ScheduleFields,
  TermsFields,
  type PickOption,
} from './offer-form-fields';
import { useOfferReview } from './offer-review-context';
import { OfferPreviewCard } from './offer-preview-card';
import { StepsField } from './offer-steps-field';

type OfferFormProps = {
  mode: 'create' | 'edit';
  offerId?: string;
  status?: OfferRowStatus;
  initialValues: OfferFormValues;
  categories: PickOption[];
  merchants: PickOption[];
  /** An archived offer cannot be edited: the form shows its values and offers no save. */
  readOnly?: boolean;
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="space-y-5 rounded-card border p-4 sm:p-6">
      <legend className="px-2 text-base font-semibold">{title}</legend>
      {children}
    </fieldset>
  );
}

/**
 * Create or edit an offer. Validation runs in the browser with the same Zod schema the server action uses
 * (react-hook-form + Zod), then the action validates again and the API once more. The first invalid field
 * takes focus; a failure that belongs to no field fills a summary that takes focus; a successful save is
 * announced and focused. While edits are unsaved the page warns before it is closed, and the status buttons next
 * to the form wait (they act on the saved version).
 */
export function OfferForm({
  mode,
  offerId,
  status,
  initialValues,
  categories,
  merchants,
  readOnly = false,
}: OfferFormProps) {
  const router = useRouter();
  const { toast } = useToast();
  const form = useForm<OfferFormValues>({
    resolver: zodResolver(offerFormSchema),
    defaultValues: initialValues,
    // The page reloads the offer after a save or when somebody else changed it: the form follows, but never
    // overwrites a field the editor has typed in.
    values: initialValues,
    resetOptions: { keepDirtyValues: true },
  });
  const review = useOfferReview();
  const [refreshing, startRefresh] = useTransition();
  const { formError, clearFormError, summaryRef, showFailure } = useServerFormErrors(
    form.setError,
    form.setFocus
  );
  const dirty = form.formState.isDirty;
  const { showSaved, savedRef, markSaved, clearSaved } = useSavedMessage(dirty);
  const submitOnce = useSubmitOnce();
  useUnsavedChanges(dirty);

  async function onSubmit(values: OfferFormValues): Promise<void> {
    clearFormError();
    clearSaved();
    review.beginWrite();
    const outcome = await settleAction(() =>
      mode === 'create' || !offerId ? createOfferAction(values) : updateOfferAction(offerId, values)
    );
    if (!outcome.success) {
      review.recordFailure(outcome.reason);
      showFailure(outcome);
      return;
    }
    if (mode === 'create') {
      toast({ title: 'Draft created', description: values.title });
      router.push(`/admin/offers/${outcome.data.id}`);
      return;
    }
    const written = outcome.data;
    review.recordWrite(
      'reviewToken' in written && typeof written.reviewToken === 'string'
        ? written.reviewToken
        : undefined
    );
    form.reset(values);
    markSaved();
    // The status buttons still hold the token of the version before this save until the reload lands.
    startRefresh(() => router.refresh());
  }

  const submitting = form.formState.isSubmitting;
  useReportFormDirty(dirty);
  useReportFormBusy(refreshing);
  const slug = status ? initialValues.slug : undefined;

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void submitOnce(() => form.handleSubmit(onSubmit)(event));
      }}
      noValidate
      aria-label={mode === 'create' ? 'New offer' : 'Edit offer'}
      className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]"
    >
      <fieldset disabled={readOnly} className="min-w-0 space-y-6 border-0 p-0">
        <Section title="Basics">
          <BasicsFields form={form} slugEditable={mode === 'create'} />
        </Section>
        <Section title="Category and merchant">
          <ClassificationFields form={form} categories={categories} merchants={merchants} />
        </Section>
        <Section title="Picture">
          <PictureFields form={form} />
        </Section>
        <Section title="Terms and claiming">
          <TermsFields form={form} />
        </Section>
        <Section title="How to claim">
          <StepsField form={form} />
        </Section>
        <Section title="Schedule and notes">
          <ScheduleFields form={form} />
        </Section>

        <FormFooter
          error={formError}
          summaryRef={summaryRef}
          showSaved={showSaved}
          savedRef={savedRef}
          dirty={dirty}
          submitting={submitting}
          submitLabel={mode === 'create' ? 'Create draft' : 'Save changes'}
          readOnly={readOnly}
        />
      </fieldset>

      <div className="lg:sticky lg:top-24 lg:self-start">
        <OfferPreviewCard
          control={form.control}
          merchants={merchants}
          status={status}
          slug={slug}
        />
      </div>
    </form>
  );
}
