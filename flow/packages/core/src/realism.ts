/**
 * 真实感判据：什么样的名字一眼就看得出是占位符，什么职业该出现什么类目。
 *
 * 这块不负责「编内容」——内容还是模型写。这里只提供两张表：
 * 一张是**不能出现**的占位词，一张是职业与消费类目的常识映射。校验规则
 * （R12/R13）拿它们判，生成提示词也拿它们举例。
 */

/** 占位公司名里最常见的词。真公司不会叫这些。 */
export const PLACEHOLDER_WORDS: readonly string[] = [
  'acme',
  'example',
  'sample',
  'fictional',
  'placeholder',
  'dummy',
  'test',
  'demo',
  'foo',
  'bar',
  'baz',
  'lorem',
  'ipsum',
  'xyz',
  'abc corp',
  'company name',
  'your company',
  'my business',
  'n/a',
  'tbd',
];

/** 收入的 ACH 描述在真账单上的形状：`ACH CREDIT <付款方> <追踪号>` 或 `<付款方> PAYMENT`。 */
export const ACH_DESCRIPTOR = /^(?:ACH (?:CREDIT|DEPOSIT) )?[A-Z0-9][A-Z0-9 &'.,-]{2,}(?: (?:PAYMENT|PMT|TRANSFER|DEPOSIT|DES:PAYROLL|TRN:\d+))?$/;

const PLACEHOLDER_RE = new RegExp(
  `\\b(?:${PLACEHOLDER_WORDS.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})\\b`,
  'i',
);

/** 名字里带占位词就算露馅（`ACME DESIGN LLC`、`Example Holdings` 都算）。 */
export function looksLikePlaceholder(text: string | undefined): boolean {
  return PLACEHOLDER_RE.test(text ?? '');
}

/**
 * 职业关键词 → 这个人账单里**应该**出现的消费类目。
 *
 * 取值是本仓库研究结果里用的 category 词（coffee / grocery / fuel / hardware …）。
 * 匹配按小写子串，先命中的先算；都不命中就返回空数组（不判）。
 */
export const OCCUPATION_CATEGORIES: readonly (readonly [RegExp, readonly string[]])[] = [
  [/barista|coffee|bak(?:er|ing)|cafe|pastry/i, ['coffee', 'grocery', 'restaurant']],
  [/contractor|carpenter|electrician|plumb|hvac|handyman|construction/i, ['hardware', 'fuel', 'grocery']],
  [/driver|delivery|courier|truck|rideshare|uber|lyft/i, ['fuel', 'convenience', 'grocery']],
  [/nurse|therapist|caregiver|medical|dental|phlebotom/i, ['pharmacy', 'coffee', 'grocery']],
  [/teacher|tutor|instructor|professor/i, ['coffee', 'online retail', 'grocery']],
  [/designer|illustrator|photographer|artist|writer|editor/i, ['art supplies', 'coffee', 'online retail']],
  [/developer|engineer|programmer|analyst|consultant/i, ['coffee', 'subscription', 'online retail']],
  [/trainer|coach|yoga|fitness|massage/i, ['grocery', 'fuel', 'subscription']],
  [/landscap|gardener|farmer|florist/i, ['hardware', 'fuel', 'grocery']],
  [/stylist|barber|salon|esthetician/i, ['online retail', 'coffee', 'grocery']],
  [/accountant|bookkeeper|tax|notary|realtor/i, ['coffee', 'subscription', 'fuel']],
  [/chef|cook|caterer|restaurant/i, ['grocery', 'restaurant', 'convenience']],
];

/** 某职业「必含」的消费类目；识别不出职业时返回空（不判）。 */
export function expectedCategories(occupation: string | undefined): readonly string[] {
  const text = occupation ?? '';
  for (const [pattern, categories] of OCCUPATION_CATEGORIES) {
    if (pattern.test(text)) return categories;
  }
  return [];
}

/**
 * 收入的描述符要不要带「付款方 + 动作」两段式。
 *
 * 真账单里 ACH 入账几乎都是 `付款方 PAYMENT` / `ACH CREDIT 付款方`，
 * 只有单独的 `DEPOSIT` 或纯数字的情况极少。
 */
export function describeIncome(payerName: string, fallback = 'ACH CREDIT'): string {
  const payer = payerName.trim().toUpperCase();
  return payer ? `${payer} PAYMENT` : fallback;
}
