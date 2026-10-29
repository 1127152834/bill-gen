import { describe, expect, it } from 'vitest';
import { fileURLToPath } from 'node:url';
import {
  bankReferenceText,
  loadBankReferences,
  loadSkills,
  referenceCoverage,
  REFERENCE_BY_TEMPLATE,
} from '../src/skills.js';

const SKILLS = fileURLToPath(new URL('../../../skills/', import.meta.url));

/** 与 Python 侧 `supported_ids()` 对齐；新增版式时两边都要加。 */
const TEMPLATES = [
  'boa-personal',
  'boa-business',
  'chase-personal',
  'chase-business',
  'usbank-personal',
  'usbank-business',
  'wellsfargo',
  'amex',
  'capitalone',
  'sofi',
  'mercury',
];

describe('bank-forms skill', () => {
  it('每次只给这一家银行的参考文件', () => {
    const skills = loadSkills(SKILLS);
    const capitalone = skills.forTemplates?.(['capitalone']) ?? '';
    expect(capitalone).toContain('CATEGORY 是品类词');
    expect(capitalone).not.toContain('Primary Account Holder'); // SoFi 的页眉形状
    expect(capitalone).not.toContain('Bank of America');
  });

  it('总带通用参考，索引也一起注入', () => {
    const skills = loadSkills(SKILLS);
    const chase = skills.forTemplates?.(['chase-personal']) ?? '';
    expect(chase).toContain('账单表单填写技能');
    expect(chase).toContain('不管哪家银行都成立');
  });

  it('同一家的个人版与企业版共用一份参考', () => {
    const refs = loadBankReferences(SKILLS);
    const personal = bankReferenceText(refs, ['chase-personal']).used;
    const business = bankReferenceText(refs, ['chase-business']).used;
    expect(personal).toEqual(business);
    expect(personal).toContain('chase');
  });

  it('没有命中银行时只给通用参考，不拿别家行文污染', () => {
    const refs = loadBankReferences(SKILLS);
    const { used } = bankReferenceText(refs, ['unknown-bank']);
    expect(used).toEqual(['common']);
  });

  it('每个版式都有参考文件（新增模板忘了写会红）', () => {
    const refs = loadBankReferences(SKILLS);
    expect(referenceCoverage(refs, TEMPLATES)).toEqual([]);
    expect(Object.keys(REFERENCE_BY_TEMPLATE).sort()).toEqual([...TEMPLATES].sort());
  });
});
