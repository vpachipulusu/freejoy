import { describe, expect, it } from 'vitest';
import {
  partnerKeyFormSchema,
  PARTNER_KEY_LABEL_MAX,
} from '@/lib/admin-catalogue/partner-key-schema';

describe('partnerKeyFormSchema', () => {
  it('accepts a plain label and trims it', () => {
    expect(partnerKeyFormSchema.parse({ label: '  Production feed ' })).toEqual({
      label: 'Production feed',
    });
  });

  it.each([
    ['empty', '   '],
    ['too long', 'x'.repeat(PARTNER_KEY_LABEL_MAX + 1)],
    ['a line break', 'one\ntwo'],
    ['a control character', 'a\u0007b'],
    ['a whole key', `fj_test_${'a'.repeat(24)}_${'0'.repeat(64)}`],
    ['the start of a key', 'my key fj_production_a1b2c3d4e5'],
  ])('refuses a label that is %s', (_name, label) => {
    const result = partnerKeyFormSchema.safeParse({ label });

    expect(result.success).toBe(false);
  });

  it('keeps ordinary text that merely mentions fj', () => {
    expect(partnerKeyFormSchema.safeParse({ label: 'fj team feed' }).success).toBe(true);
  });
});
