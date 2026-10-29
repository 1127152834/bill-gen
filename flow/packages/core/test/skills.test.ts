import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { SKILLS_BY_NODE, loadSkills, type SkillName } from '../src/index.js';

const SKILLS_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '../../../skills');
const ALL: SkillName[] = [
  'bank-descriptors',
  'self-employed-patterns',
  'industry-cost-structures',
  'us-calendar-seasonality',
  'consumer-habits',
];

const lines = (name: SkillName): string[] => readFileSync(resolve(SKILLS_DIR, `${name}.md`), 'utf8').split('\n');

describe('loadSkills', () => {
  const store = loadSkills(SKILLS_DIR);

  it('returns a section headed "## skill: <name>" containing the file content', () => {
    const text = store.get(['bank-descriptors']);
    expect(text.startsWith('## skill: bank-descriptors')).toBe(true);
    expect(text).toContain('TST*');
  });

  it('concatenates in the order requested, one section per skill', () => {
    const text = store.get(['consumer-habits', 'bank-descriptors']);
    expect(text.indexOf('## skill: consumer-habits')).toBeGreaterThanOrEqual(0);
    expect(text.indexOf('## skill: consumer-habits')).toBeLessThan(text.indexOf('## skill: bank-descriptors'));
    expect(text.match(/^## skill: /gm)).toHaveLength(2);
  });

  it('throws on an unknown skill name', () => {
    expect(() => store.get(['no-such-skill' as SkillName])).toThrow(/no-such-skill/);
  });

  it('throws when the skills directory is missing a file', () => {
    expect(() => loadSkills(resolve(SKILLS_DIR, 'nope'))).toThrow();
  });
});

describe('SKILLS_BY_NODE', () => {
  it.each(Object.entries(SKILLS_BY_NODE))('%s: every entry can be passed straight to get()', (_node, names) => {
    const text = loadSkills(SKILLS_DIR).get(names);
    for (const name of names) expect(text).toContain(`## skill: ${name}`);
    expect(text.match(/^## skill: /gm)).toHaveLength(names.length);
  });

  it('maps every node to the agreed skills', () => {
    expect(SKILLS_BY_NODE.planResearch).toEqual(['self-employed-patterns', 'industry-cost-structures']);
    expect(SKILLS_BY_NODE.lifeModel).toEqual([
      'self-employed-patterns',
      'industry-cost-structures',
      'us-calendar-seasonality',
      'consumer-habits',
    ]);
    expect(SKILLS_BY_NODE.descriptors).toEqual(['bank-descriptors']);
    expect(SKILLS_BY_NODE.audit).toHaveLength(5);
    expect(new Set(SKILLS_BY_NODE.audit)).toEqual(new Set(ALL));
  });
});

describe('skill files', () => {
  it.each(ALL)('%s: 150-400 lines, ends with a Sources list of URLs', (name) => {
    const ls = lines(name);
    expect(ls.length).toBeGreaterThanOrEqual(150);
    expect(ls.length).toBeLessThanOrEqual(400);
    const at = ls.findIndex((l) => /^#{2,3} Sources/.test(l));
    expect(at, 'Sources heading').toBeGreaterThan(0);
    const urls = ls.slice(at).filter((l) => /https?:\/\//.test(l));
    expect(urls.length).toBeGreaterThanOrEqual(3);
    // nothing but the list follows the heading
    expect(ls.slice(at + 1).every((l) => l.trim() === '' || /^[-*] .*https?:\/\//.test(l))).toBe(true);
  });

  it('bank-descriptors covers every required descriptor family', () => {
    const t = lines('bank-descriptors').join('\n');
    for (const needle of ['CHECKCARD', 'POS', 'ACH', 'SQ *', 'TST*', 'AMZN Mktp', 'Zelle', 'Venmo', 'PAYPAL', 'USATAXPYMT', 'ATM', 'INTEREST', 'FEE']) {
      expect(t, needle).toContain(needle);
    }
    expect(t).toMatch(/card payment/i);
  });

  it('self-employed-patterns has at least 12 trade sections', () => {
    const trades = lines('self-employed-patterns').filter((l) => /^### Trade: /.test(l));
    expect(trades.length).toBeGreaterThanOrEqual(12);
  });

  it('industry-cost-structures has at least 10 industry sections', () => {
    const industries = lines('industry-cost-structures').filter((l) => /^### Industry: /.test(l));
    expect(industries.length).toBeGreaterThanOrEqual(10);
  });

  it('contains no routing numbers, full card numbers or real-bank format descriptions', () => {
    const banks = /\b(chase|wells fargo|bank of america|bofa|us bank|u\.s\. bank|citibank|capital one|american express|amex|ally|alliant|pnc|truist|discover)\b/i;
    for (const name of ALL) {
      // URLs (source ids) are not content: check only prose lines.
      const t = lines(name).filter((l) => !/https?:\/\//.test(l)).join('\n');
      expect(t, `${name} bank brand`).not.toMatch(banks);
      expect(t, `${name} 9-digit routing`).not.toMatch(/\b\d{9}\b/);
      expect(t, `${name} 13-19 digit card`).not.toMatch(/\b\d{13,19}\b/);
    }
  });
});
