import { describe, expect, it } from 'vitest';
import { disclosureBouncePath, forcesDisclosure, isExactMarker } from '@/lib/outbound/disclosure';

describe('isExactMarker', () => {
  it('is true only for exactly one value that is exactly 1', () => {
    expect(isExactMarker(['1'])).toBe(true);
    for (const values of [
      [],
      ['0'],
      ['true'],
      [''],
      ['01'],
      ['1 '],
      ['1', '1'],
      ['1', '0'],
      ['0', '1'],
    ]) {
      expect(isExactMarker(values)).toBe(false);
    }
  });
});

describe('forcesDisclosure (the ad parameter of the offer page)', () => {
  it('is true for the single exact value 1', () => {
    expect(forcesDisclosure('1')).toBe(true);
    expect(forcesDisclosure(['1'])).toBe(true);
  });

  it.each([
    undefined,
    '',
    '0',
    'true',
    'yes',
    '01',
    ' 1',
    '1 ',
    '١',
    ['1', '1'],
    ['1', '0'],
    ['0'],
    [],
  ])('is false for %j: it can never remove a disclosure, only add one', (value) => {
    expect(forcesDisclosure(value as string | string[] | undefined)).toBe(false);
  });
});

describe('disclosureBouncePath', () => {
  it('is a relative path to the offer page with ad=1 and nothing else', () => {
    const path = disclosureBouncePath('free-shampoo');

    expect(path).toBe('/freebies/free-shampoo?ad=1');
    expect(path.startsWith('/') && !path.startsWith('//')).toBe(true);
  });
});
