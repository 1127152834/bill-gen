import { describe, expect, it } from 'vitest';
import { expectedCategories, describeIncome, looksLikePlaceholder } from '../src/realism.js';

describe('looksLikePlaceholder', () => {
  it('flags the standard placeholder vocabulary', () => {
    for (const name of ['ACME CORP', 'Example Holdings', 'SAMPLE LLC', 'Fictional Co', 'TEST VENDOR', 'Demo Shop', 'XYZ Inc', 'Foo Bar LLC']) {
      expect(looksLikePlaceholder(name)).toBe(true);
    }
  });

  it('leaves ordinary company names alone', () => {
    for (const name of ['KESTREL DESIGN CO', 'Northgate Dental', 'Pine & Porter', 'LUMEN CREATIVE LLC', 'Gusto', 'IRS Direct Pay']) {
      expect(looksLikePlaceholder(name)).toBe(false);
    }
  });

  it('says nothing about an undefined name', () => {
    expect(looksLikePlaceholder(undefined)).toBe(false);
  });
});

describe('expectedCategories', () => {
  it('maps a trade to the categories its statement should show', () => {
    expect(expectedCategories('Freelance designer')).toEqual(['art supplies', 'coffee', 'online retail']);
    expect(expectedCategories('barista')).toEqual(['coffee', 'grocery', 'restaurant']);
    expect(expectedCategories('Rideshare driver')).toEqual(['fuel', 'convenience', 'grocery']);
  });

  it('returns nothing for an unknown occupation so the rule stays quiet', () => {
    expect(expectedCategories('underwater basket weaver')).toEqual([]);
    expect(expectedCategories(undefined)).toEqual([]);
  });
});

describe('describeIncome', () => {
  it('puts the payer first, the way a real ACH row reads', () => {
    expect(describeIncome('Kestrel Design Co')).toBe('KESTREL DESIGN CO PAYMENT');
  });

  it('falls back when there is no payer', () => {
    expect(describeIncome('   ')).toBe('ACH CREDIT');
  });
});
