'use client';

import { useRouter } from 'next/navigation';
import { announceNavigation } from '@/components/site/navigation-events';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { createCategoryAction, updateCategoryAction } from '@/app/actions/admin/category-actions';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { categoryFormSchema, type CategoryFormValues } from '@/lib/admin-catalogue/category-schema';
import { settleAction } from '@/lib/admin-catalogue/settle-action';
import { tintLabel } from '@/lib/admin-catalogue/offer-labels';
import { TINT_VALUES } from '@/lib/admin-catalogue/offer-schema';
import { useToast } from '@/hooks/use-toast';
import { Field } from '../_components/form-field';
import { FormFooter } from '../_components/form-footer';
import { NativeSelect } from '../_components/native-select';
import {
  useSavedMessage,
  useServerFormErrors,
  useSubmitOnce,
} from '../_components/use-server-form-errors';
import { useUnsavedChanges } from '../_components/use-unsaved-changes';

type CategoryFormProps = {
  mode: 'create' | 'edit';
  categoryId?: string;
  initialValues: CategoryFormValues;
};

/** Create or edit a category (react-hook-form + the same Zod schema the server action uses). */
export function CategoryForm({ mode, categoryId, initialValues }: CategoryFormProps) {
  const router = useRouter();
  const { toast } = useToast();
  const form = useForm<CategoryFormValues>({
    resolver: zodResolver(categoryFormSchema),
    defaultValues: initialValues,
  });
  const { register, formState, handleSubmit } = form;
  const { errors, isSubmitting } = formState;
  const { formError, clearFormError, summaryRef, showFailure } = useServerFormErrors(
    form.setError,
    form.setFocus
  );
  const dirty = form.formState.isDirty;
  const { showSaved, savedRef, markSaved, clearSaved } = useSavedMessage(dirty);
  const submitOnce = useSubmitOnce();
  useUnsavedChanges(dirty);

  async function onSubmit(values: CategoryFormValues): Promise<void> {
    clearFormError();
    clearSaved();
    const outcome = await settleAction(() =>
      mode === 'create' || !categoryId
        ? createCategoryAction(values)
        : updateCategoryAction(categoryId, values)
    );
    if (!outcome.success) {
      showFailure(outcome);
      return;
    }
    if (mode === 'create') {
      toast({ title: 'Category created', description: values.name });
      announceNavigation('/admin/categories');
      router.push('/admin/categories');
      return;
    }
    form.reset(values);
    markSaved();
    router.refresh();
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void submitOnce(() => handleSubmit(onSubmit)(event));
      }}
      noValidate
      aria-label={mode === 'create' ? 'New category' : 'Edit category'}
      className="max-w-2xl space-y-5"
    >
      <Field id="category-name" label="Name" required error={errors.name?.message}>
        {(aria) => <Input id="category-name" maxLength={80} {...aria} {...register('name')} />}
      </Field>
      <Field
        id="category-slug"
        label="Slug"
        error={errors.slug?.message}
        hint={
          mode === 'create'
            ? 'The web address of the category page. Leave blank to use the name. It cannot be changed later.'
            : 'The web address of the category page. It cannot be changed.'
        }
      >
        {(aria) => (
          <Input
            id="category-slug"
            maxLength={80}
            autoCapitalize="none"
            spellCheck={false}
            readOnly={mode === 'edit'}
            {...aria}
            {...register('slug')}
          />
        )}
      </Field>
      <Field
        id="category-short-name"
        label="Short name"
        required
        error={errors.shortName?.message}
        hint="Used on chips and the phone grid."
      >
        {(aria) => (
          <Input id="category-short-name" maxLength={40} {...aria} {...register('shortName')} />
        )}
      </Field>
      <Field
        id="category-description"
        label="Description"
        required
        error={errors.description?.message}
        hint="One sentence for cards and search results."
      >
        {(aria) => (
          <Textarea id="category-description" rows={2} {...aria} {...register('description')} />
        )}
      </Field>
      <Field id="category-intro" label="Introduction" required error={errors.intro?.message}>
        {(aria) => <Textarea id="category-intro" rows={4} {...aria} {...register('intro')} />}
      </Field>
      <div className="grid gap-5 sm:grid-cols-2">
        <Field id="category-tint" label="Tint" required error={errors.tint?.message}>
          {(aria) => (
            <NativeSelect id="category-tint" {...aria} {...register('tint')}>
              {TINT_VALUES.map((value) => (
                <option key={value} value={value}>
                  {tintLabel(value)}
                </option>
              ))}
            </NativeSelect>
          )}
        </Field>
        <Field
          id="category-sort"
          label="Sort order"
          required
          error={errors.sortOrder?.message}
          hint="Lower numbers come first."
        >
          {(aria) => (
            <Input id="category-sort" inputMode="numeric" {...aria} {...register('sortOrder')} />
          )}
        </Field>
      </div>
      {mode === 'create' ? (
        <label className="flex min-h-11 items-center gap-3 text-sm font-medium">
          <input
            type="checkbox"
            className="size-5 shrink-0 accent-[hsl(var(--primary))]"
            {...register('enabled')}
          />
          Enabled (visible on the public site)
        </label>
      ) : null}

      <FormFooter
        error={formError}
        summaryRef={summaryRef}
        showSaved={showSaved}
        savedRef={savedRef}
        dirty={dirty}
        submitting={isSubmitting}
        submitLabel={mode === 'create' ? 'Create category' : 'Save changes'}
      />
    </form>
  );
}
