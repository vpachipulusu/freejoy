import { z } from 'zod';
import { isSingleLine } from './text-rules';

/** The create-key form, mirrored from `app/api/v1/admin_partner_keys.py` (the API stays the authority). */

export const PARTNER_KEY_LABEL_MAX = 80;

/** A label that carries a key (or the start of one) would keep the secret in plain text, so it is refused. */
const KEY_LOOKING = /fj_[a-z0-9]{1,16}_[a-z0-9]{8,}/i;

export const partnerKeyFormSchema = z.object({
  label: z
    .string()
    .trim()
    .min(1, 'Enter a label, for example "Production feed".')
    .max(PARTNER_KEY_LABEL_MAX, `Keep this to ${PARTNER_KEY_LABEL_MAX} characters or fewer.`)
    .refine(isSingleLine, 'Remove line breaks and control characters.')
    .refine(
      (value) => !KEY_LOOKING.test(value),
      'A label is a name, not a key. Do not paste a key.'
    ),
});

export type PartnerKeyFormValues = z.infer<typeof partnerKeyFormSchema>;

/** A key as the admin sees it. It never holds the key itself or its hash. */
export interface PartnerKeyView {
  id: string;
  label: string;
  createdAt: string;
  lastUsedAt: string | null;
  revokedAt: string | null;
}

/** What creating a key returns to the browser: the view, and the key itself, shown once. */
export interface IssuedPartnerKeyView {
  key: PartnerKeyView;
  apiKey: string;
}
