/**
 * Realism "skills": markdown knowledge files (repo-root `skills/`) injected into LLM prompts.
 * `loadSkills` reads them once; `get` returns the requested ones as `## skill: <name>` sections.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

export const SKILL_NAMES = [
  'bank-descriptors',
  'self-employed-patterns',
  'industry-cost-structures',
  'us-calendar-seasonality',
  'consumer-habits',
] as const;

export type SkillName = (typeof SKILL_NAMES)[number];

export type SkillStore = { get(names: readonly SkillName[]): string };

/** Which skills each LLM node receives. */
export const SKILLS_BY_NODE = {
  planResearch: ['self-employed-patterns', 'industry-cost-structures'],
  lifeModel: ['self-employed-patterns', 'industry-cost-structures', 'us-calendar-seasonality', 'consumer-habits'],
  descriptors: ['bank-descriptors'],
  audit: [...SKILL_NAMES],
} as const satisfies Record<string, readonly SkillName[]>;

export type SkillNode = keyof typeof SKILLS_BY_NODE;

const isSkillName = (name: string): name is SkillName => (SKILL_NAMES as readonly string[]).includes(name);

/** Reads every skill file from `dir`; throws if any is missing so a bad deploy fails at startup. */
export function loadSkills(dir: string): SkillStore {
  const sections = new Map<SkillName, string>();
  for (const name of SKILL_NAMES) {
    const body = readFileSync(join(dir, `${name}.md`), 'utf8').trim();
    sections.set(name, `## skill: ${name}\n\n${body}\n`);
  }
  return {
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
