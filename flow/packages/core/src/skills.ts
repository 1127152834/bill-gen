/**
 * Realism "skills": markdown knowledge files (repo-root `skills/`) injected into LLM prompts.
 * `loadSkills` reads them once; `get` returns the requested ones as `## skill: <name>` sections.
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

export const SKILL_NAMES = [
  'bank-descriptors',
  'self-employed-patterns',
  'industry-cost-structures',
  'us-calendar-seasonality',
  'consumer-habits',
] as const;

export type SkillName = (typeof SKILL_NAMES)[number];

/**
 * 每套版式对应哪份 `references/*.md`。版式 id 形如 `chase-personal`，参考文件形如 `chase.md`，
 * 所以按前缀归并；新增版式时如果忘了写参考文件，`referenceCoverage()` 会把它报出来。
 */
export const REFERENCE_BY_TEMPLATE: Record<string, string> = {
  'boa-personal': 'boa',
  'boa-business': 'boa',
  'chase-personal': 'chase',
  'chase-business': 'chase',
  'usbank-personal': 'usbank',
  'usbank-business': 'usbank',
  'wellsfargo': 'wellsfargo',
  'capitalone': 'capitalone',
  'sofi': 'sofi',
  'mercury': 'mercury',
};

export type SkillStore = {
  /** 整套技能文本（历史行为：全部注入）。 */
  get(names: readonly SkillName[]): string;
  /**
   * 只注入与主体相关的章节。技能文件里 `### Trade: …` / `### Industry: …` 各有十几块，
   * 一个主体只对应其中一块，其余全是噪声——每轮都重发一遍，白等输入处理时间。
   */
  getSelective(names: readonly SkillName[], keywords: readonly string[]): string;
  /**
   * 版式表单技能：`bank-forms/SKILL.md` + 命中模板的 `references/*.md`。
   *
   * 只给这一次要出片的银行，模型不用在 8 家规范里挑——按版式注入，既省 token 又不会串行文。
   */
  forTemplates?(templates: readonly string[]): string;
};

/** Which skills each LLM node receives. */
export const SKILLS_BY_NODE = {
  planResearch: ['self-employed-patterns', 'industry-cost-structures'],
  lifeModel: ['self-employed-patterns', 'industry-cost-structures', 'us-calendar-seasonality', 'consumer-habits'],
  descriptors: ['bank-descriptors'],
  audit: [...SKILL_NAMES],
} as const satisfies Record<string, readonly SkillName[]>;

export type SkillNode = keyof typeof SKILLS_BY_NODE;

/**
 * 生活模型这一节点的知识文本。
 *
 * 四个技能文件加起来 ~53KB（≈14k token），每轮都会重发一次，是 lifeModel 最慢的一段输入。
 * 个人主体的「行业成本结构」只在做企业账时才有用（个人消费的价格已经由 researched facts 给出），
 * 所以个人档不再带它——省下 ~14KB，规则强度不变。
 */
/**
 * 把一份技能文件裁到「和这个主体有关」的章节。
 *
 * 规则很简单，但能把 15KB 的 trades 列表裁成 5KB：
 * - `## ` 一级小节整体保留（它们是通用规则）；
 * - 含 `### ` 子块的小节（Trade / Industry）里，只留标题命中 keywords 的那一块；
 *   一块都不命中时保留该小节的引导段（子块之前的文字），并留一句「本类目不适用」的说明。
 */
export function selectSkillSections(body: string, keywords: readonly string[]): string {
  const words = keywords.map((k) => k.trim().toLowerCase()).filter((k) => k.length >= 3);
  const lines = body.split('\n');
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (!line.startsWith('## ') && !line.startsWith('# ')) {
      out.push(line);
      i++;
      continue;
    }
    // 收集这个小节（直到下一个 `## ` / `# `）
    const start = i;
    i++;
    while (i < lines.length && !lines[i]!.startsWith('## ') && !lines[i]!.startsWith('# ')) i++;
    const block = lines.slice(start, i);
    const subStarts = block.flatMap((l, idx) => (l.startsWith('### ') ? [idx] : []));
    // A `## Sources` block is a citation list: it tells the reader where a number came from, not
    // what to do with it. Dropping it keeps the provenance in the file while the prompt stays lean.
    if (/^##\s*(\d+\.\s*)?sources\b/i.test(block[0] ?? '')) continue;
    if (subStarts.length === 0 || words.length === 0) {
      out.push(...block);
      continue;
    }
    const intro = block.slice(0, subStarts[0]!);
    const subs: string[][] = subStarts.map((from, idx) => block.slice(from, subStarts[idx + 1] ?? block.length));
    const picked = subs.filter((sub) => {
      const heading = (sub[0] ?? '').toLowerCase();
      return words.some((w) => heading.includes(w));
    });
    out.push(...intro);
    if (picked.length > 0) out.push(...picked.flat());
    else out.push('（本小节没有与该主体匹配的条目，按上面的通用规则处理。）', '');
  }
  return out.join('\n');
}

export function lifeModelSkills(isBusiness: boolean): readonly SkillName[] {
  return isBusiness
    ? SKILLS_BY_NODE.lifeModel
    : (['self-employed-patterns', 'us-calendar-seasonality', 'consumer-habits'] as const);
}

const isSkillName = (name: string): name is SkillName => (SKILL_NAMES as readonly string[]).includes(name);

/** Reads every skill file from `dir`; throws if any is missing so a bad deploy fails at startup. */
/** 目录式技能的参考文件：`<dir>/bank-forms/references/*.md`，按文件名（去扩展名）索引。 */
export function loadBankReferences(dir: string): Map<string, string> {
  const refDir = join(dir, 'bank-forms', 'references');
  if (!existsSync(refDir)) return new Map();
  const out = new Map<string, string>();
  for (const file of readdirSync(refDir)) {
    if (!file.endsWith('.md')) continue;
    out.set(file.slice(0, -3), readFileSync(join(refDir, file), 'utf8').trim());
  }
  return out;
}

/** `bank-forms/SKILL.md`：索引 + 通用规矩，永远随参考文件一起注入。 */
export function loadBankSkillIndex(dir: string): string {
  const file = join(dir, 'bank-forms', 'SKILL.md');
  return existsSync(file) ? readFileSync(file, 'utf8').trim() : '';
}

/**
 * 这次要出片的银行，各自的参考文件正文。
 *
 * - 命中模板 → 对应的 references/<bank>.md；
 * - 同时总带 `common.md`（金额/日期/身份这些跨行规矩）；
 * - 一个都没命中时返回空串（不拿别家的行文去污染提示词）。
 */
export function bankReferenceText(
  references: Map<string, string>,
  templates: readonly string[],
): { text: string; used: string[] } {
  const picked = new Set<string>();
  if (references.has('common')) picked.add('common');
  for (const template of templates) {
    const bank = REFERENCE_BY_TEMPLATE[template];
    if (bank && references.has(bank)) picked.add(bank);
  }
  const used = [...picked];
  const text = used
    .map((name) => `### reference: ${name}.md\n\n${references.get(name) ?? ''}`)
    .join('\n\n');
  return { text, used };
}

/** 哪些模板还没有参考文件（新增版式时的守卫）。 */
export function referenceCoverage(references: Map<string, string>, templates: readonly string[]): string[] {
  return templates.filter((template) => {
    const bank = REFERENCE_BY_TEMPLATE[template];
    return !bank || !references.has(bank);
  });
}

export function loadSkills(dir: string): SkillStore {
  const sections = new Map<SkillName, string>();
  const references = loadBankReferences(dir);
  const bankIndex = loadBankSkillIndex(dir);
  for (const name of SKILL_NAMES) {
    const body = readFileSync(join(dir, `${name}.md`), 'utf8').trim();
    sections.set(name, `## skill: ${name}\n\n${body}\n`);
  }
  const section = (name: SkillName): string => {
    const body = sections.get(name);
    if (body === undefined) throw new Error(`Unknown skill: ${String(name)}`);
    return body;
  };
  return {
    getSelective(names, keywords) {
      return names.map((name) => `## skill: ${name}\n\n${selectSkillSections(section(name), keywords)}\n`).join('\n');
    },
    forTemplates(templates) {
      const { text } = bankReferenceText(references, templates);
      const head = bankIndex ? `## skill: bank-forms\n\n${bankIndex}\n` : '';
      if (!head && !text) return '';
      return [head, text].filter(Boolean).join('\n');
    },
    get(names) {
      return names
        .map((name) => {
          const section = isSkillName(name) ? sections.get(name) : undefined;
          if (section === undefined) throw new Error(`Unknown skill: ${String(name)}`);
          return section;
        })
        .join('\n');
    },
  };
}
