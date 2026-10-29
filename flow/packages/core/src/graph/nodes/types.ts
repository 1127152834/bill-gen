import type { StructuredLlm } from '../../llm.js';
import type { SkillStore } from '../../skills.js';

/** What every model node needs: the structured model client and the realism skills. */
export type NodeDeps = { llm: StructuredLlm; skills: SkillStore };
