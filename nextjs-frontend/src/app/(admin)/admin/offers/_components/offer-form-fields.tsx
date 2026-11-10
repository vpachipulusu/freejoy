'use client';

import type { UseFormReturn } from 'react-hook-form';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  COMMERCIAL_BASIS_VALUES,
  FRESHNESS_VALUES,
  OFFER_TYPE_VALUES,
  POSTAGE_KIND_VALUES,
  TINT_VALUES,
  type OfferFormValues,
} from '@/lib/admin-catalogue/offer-schema';
import {
  COMMERCIAL_BASIS_HELP,
  COMMERCIAL_BASIS_LABELS,
  freshnessLabel,
  offerTypeLabel,
  POSTAGE_KIND_LABELS,
  tintLabel,
} from '@/lib/admin-catalogue/offer-labels';
import { Field } from '../../_components/form-field';
import { NativeSelect } from '../../_components/native-select';

export type PickOption = { id: string; name: string };

type SectionProps = { form: UseFormReturn<OfferFormValues> };

const CHECK_ROW = 'flex min-h-11 items-center gap-3 text-sm font-medium';
const CHECK_BOX = 'size-5 shrink-0 accent-[hsl(var(--primary))]';

export function BasicsFields({ form, slugEditable }: SectionProps & { slugEditable: boolean }) {
  const { register, formState } = form;
  const { errors } = formState;
  return (
    <div className="space-y-5">
      <Field id="offer-title" label="Title" required error={errors.title?.message}>
        {(aria) => <Input id="offer-title" maxLength={200} {...aria} {...register('title')} />}
      </Field>
      <Field
        id="offer-slug"
        label="Slug"
        error={errors.slug?.message}
        hint={
          slugEditable
            ? 'The web address of the public page. Leave blank to use the title. It cannot be changed later.'
            : 'The web address of the public page. It cannot be changed.'
        }
      >
        {(aria) => (
          <Input
            id="offer-slug"
            maxLength={80}
            autoCapitalize="none"
            spellCheck={false}
            readOnly={!slugEditable}
            {...aria}
            {...register('slug')}
          />
        )}
      </Field>
      <Field id="offer-summary" label="Summary" required error={errors.summary?.message}>
        {(aria) => <Input id="offer-summary" maxLength={300} {...aria} {...register('summary')} />}
      </Field>
      <Field
        id="offer-description"
        label="Description"
        error={errors.description?.message}
        hint="Separate paragraphs with a blank line. Up to 20 paragraphs."
      >
        {(aria) => (
          <Textarea id="offer-description" rows={6} {...aria} {...register('description')} />
        )}
      </Field>
    </div>
  );
}

export function ClassificationFields({
  form,
  categories,
  merchants,
}: SectionProps & { categories: PickOption[]; merchants: PickOption[] }) {
  const { register, formState } = form;
  const { errors } = formState;
  return (
    <div className="grid gap-5 sm:grid-cols-2">
      <Field id="offer-category" label="Category" required error={errors.categoryId?.message}>
        {(aria) => (
          <NativeSelect id="offer-category" {...aria} {...register('categoryId')}>
            <option value="">Choose a category</option>
            {categories.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </NativeSelect>
        )}
      </Field>
      <Field id="offer-merchant" label="Merchant" required error={errors.merchantId?.message}>
        {(aria) => (
          <NativeSelect id="offer-merchant" {...aria} {...register('merchantId')}>
            <option value="">Choose a merchant</option>
            {merchants.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </NativeSelect>
        )}
      </Field>
      <Field id="offer-type" label="Offer type" required error={errors.offerType?.message}>
        {(aria) => (
          <NativeSelect id="offer-type" {...aria} {...register('offerType')}>
            {OFFER_TYPE_VALUES.map((value) => (
              <option key={value} value={value}>
                {offerTypeLabel(value)}
              </option>
            ))}
          </NativeSelect>
        )}
      </Field>
      <Field
        id="offer-freshness"
        label="Freshness label"
        error={errors.freshness?.message}
        hint="An editorial label shown on the card. Never worked out from the date."
      >
        {(aria) => (
          <NativeSelect id="offer-freshness" {...aria} {...register('freshness')}>
            <option value="">None</option>
            {FRESHNESS_VALUES.map((value) => (
              <option key={value} value={value}>
                {freshnessLabel(value)}
              </option>
            ))}
          </NativeSelect>
        )}
      </Field>
      <label className={CHECK_ROW}>
        <input type="checkbox" className={CHECK_BOX} {...register('featured')} />
        Featured on the home page
      </label>
    </div>
  );
}

const BASIS_LABEL_ID = 'offer-basis-label';
const BASIS_ERROR_ID = 'offer-basis-error';

/**
 * The commercial basis: a required choice with nothing selected to start with and one line of help under each
 * option. Radios, so every option and its help is on screen at once; the group is named and carries the error.
 */
export function CommercialBasisFields({ form }: SectionProps) {
  const { register, formState } = form;
  const error = formState.errors.commercialBasis?.message;
  return (
    <div className="space-y-3">
      <p id={BASIS_LABEL_ID} className="text-sm font-medium leading-none">
        Commercial basis
        <span className="ms-1 text-destructive" aria-hidden="true">
          *
        </span>
      </p>
      <div
        role="radiogroup"
        aria-labelledby={BASIS_LABEL_ID}
        aria-required="true"
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? BASIS_ERROR_ID : undefined}
        className="space-y-2"
      >
        {COMMERCIAL_BASIS_VALUES.map((value) => (
          <label key={value} className="flex min-h-11 items-start gap-3 text-sm">
            <input
              type="radio"
              value={value}
              className={`${CHECK_BOX} mt-0.5`}
              aria-describedby={`offer-basis-${value}-help`}
              {...register('commercialBasis')}
            />
            <span>
              <span className="block font-medium">{COMMERCIAL_BASIS_LABELS[value]}</span>
              <span id={`offer-basis-${value}-help`} className="block text-muted-foreground">
                {COMMERCIAL_BASIS_HELP[value]}
              </span>
            </span>
          </label>
        ))}
      </div>
      {error ? (
        <p id={BASIS_ERROR_ID} className="text-sm font-medium text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function PictureFields({ form }: SectionProps) {
  const { register, formState } = form;
  const { errors } = formState;
  return (
    <div className="grid gap-5 sm:grid-cols-2">
      <Field id="offer-tint" label="Picture tint" required error={errors.imageTint?.message}>
        {(aria) => (
          <NativeSelect id="offer-tint" {...aria} {...register('imageTint')}>
            {TINT_VALUES.map((value) => (
              <option key={value} value={value}>
                {tintLabel(value)}
              </option>
            ))}
          </NativeSelect>
        )}
      </Field>
      <Field
        id="offer-image-label"
        label="Picture label"
        required
        error={errors.imageLabel?.message}
        hint="Short text drawn on the illustration."
      >
        {(aria) => (
          <Input id="offer-image-label" maxLength={40} {...aria} {...register('imageLabel')} />
        )}
      </Field>
      <Field
        id="offer-image-alt"
        label="Picture description"
        required
        className="sm:col-span-2"
        error={errors.imageAlt?.message}
        hint="Alt text for people who cannot see the picture."
      >
        {(aria) => (
          <Input id="offer-image-alt" maxLength={200} {...aria} {...register('imageAlt')} />
        )}
      </Field>
    </div>
  );
}

export function TermsFields({ form }: SectionProps) {
  const { register, formState, watch } = form;
  const { errors } = formState;
  const costsMoney = watch('postageKind') === 'COST';
  return (
    <div className="space-y-5">
      <div className="grid gap-5 sm:grid-cols-2">
        <Field id="offer-postage" label="Postage" required error={errors.postageKind?.message}>
          {(aria) => (
            <NativeSelect id="offer-postage" {...aria} {...register('postageKind')}>
              {POSTAGE_KIND_VALUES.map((value) => (
                <option key={value} value={value}>
                  {POSTAGE_KIND_LABELS[value]}
                </option>
              ))}
            </NativeSelect>
          )}
        </Field>
        {costsMoney ? (
          <Field
            id="offer-postage-amount"
            label="Postage cost (GBP)"
            required
            error={errors.postageAmount?.message}
            hint="In pounds, for example 2.99."
          >
            {(aria) => (
              <Input
                id="offer-postage-amount"
                inputMode="decimal"
                {...aria}
                {...register('postageAmount')}
              />
            )}
          </Field>
        ) : null}
      </div>
      <label className={CHECK_ROW}>
        <input type="checkbox" className={CHECK_BOX} {...register('requiresCard')} />
        Needs a payment card to claim
      </label>
      <Field
        id="offer-subscription"
        label="Subscription terms"
        error={errors.subscriptionTerms?.message}
        hint="What happens after a free trial, if anything. Leave blank when there is no subscription."
      >
        {(aria) => (
          <Textarea id="offer-subscription" rows={3} {...aria} {...register('subscriptionTerms')} />
        )}
      </Field>
      <Field
        id="offer-eligibility"
        label="Who can claim"
        required
        error={errors.eligibility?.message}
      >
        {(aria) => (
          <Textarea id="offer-eligibility" rows={3} {...aria} {...register('eligibility')} />
        )}
      </Field>
      <Field
        id="offer-availability"
        label="Availability"
        required
        error={errors.availability?.message}
      >
        {(aria) => (
          <Textarea id="offer-availability" rows={3} {...aria} {...register('availability')} />
        )}
      </Field>
      <Field
        id="offer-source-url"
        label="Source URL"
        error={errors.sourceUrl?.message}
        hint="The merchant's own page. An offer needs this or an active affiliate link before it can be published."
      >
        {(aria) => (
          <Input
            id="offer-source-url"
            type="url"
            inputMode="url"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="https://"
            {...aria}
            {...register('sourceUrl')}
          />
        )}
      </Field>
    </div>
  );
}

export function ScheduleFields({ form }: SectionProps) {
  const { register, formState } = form;
  const { errors } = formState;
  return (
    <div className="space-y-5">
      <div className="grid gap-5 sm:grid-cols-2">
        <Field
          id="offer-start"
          label="Starts (UTC)"
          error={errors.startAt?.message}
          hint="Optional. Date and time in UTC."
        >
          {(aria) => (
            <Input id="offer-start" type="datetime-local" {...aria} {...register('startAt')} />
          )}
        </Field>
        <Field
          id="offer-end"
          label="Ends (UTC)"
          error={errors.endAt?.message}
          hint="Optional. Date and time in UTC."
        >
          {(aria) => (
            <Input id="offer-end" type="datetime-local" {...aria} {...register('endAt')} />
          )}
        </Field>
      </div>
      <Field
        id="offer-notes"
        label="Publisher notes"
        error={errors.publisherNotes?.message}
        hint="For admins only. Never shown to visitors."
      >
        {(aria) => <Textarea id="offer-notes" rows={3} {...aria} {...register('publisherNotes')} />}
      </Field>
    </div>
  );
}
