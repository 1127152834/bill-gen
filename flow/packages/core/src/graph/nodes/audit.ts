import { z } from 'zod';
import type { NormalizedRequest } from '../../normalize.js';
import { MAX_FINDINGS, buildAuditMessages, refPrefix } from '../../prompts/audit.js';
import { AuditFindingSchema, type AuditFinding, type MerchantFact, type Profile, type Statement } from '../../schemas/index.js';
import { SKILLS_BY_NODE } from '../../skills.js';
import type { NodeDeps } from './types.js';

const ReplySchema = z.array(AuditFindingSchema);
const SEVERITY_ORDER: Record<AuditFinding['severity'], number> = { high: 0, medium: 1, low: 2 };

export type AuditInput = { norm: NormalizedRequest; profile: Profile; statements: Statement[]; facts: MerchantFact[] };

/**
 * One model call reviewing the statements as compact tables. Findings cite short row refs
 * (CHK3, CC12); code maps them back to transaction ids (a cited real id is kept too, unknown refs
 * are dropped) and returns the findings most severe first.
 */
export async function auditNode(input: AuditInput, deps: NodeDeps): Promise<AuditFinding[]> {
  const { norm, profile, statements, facts } = input;
  const idByRef = new Map<string, string>();
  for (const s of statements) {
    s.transactions.forEach((t, i) => {
      idByRef.set(`${refPrefix(s)}${i + 1}`, t.id);
      idByRef.set(t.id, t.id);
    });
  }
  const skillsText = deps.skills.get(SKILLS_BY_NODE.audit);
  const reply = await deps.llm.invoke('audit', ReplySchema, buildAuditMessages({ norm, profile, statements, facts, skillsText }));

  return reply
    .map((f) => ({
      ...f,
      transactionIds: [...new Set(f.transactionIds.map((r) => idByRef.get(r.trim().toUpperCase()) ?? idByRef.get(r.trim())))].filter(
        (id): id is string => id !== undefined,
      ),
    }))
    .sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity])
    .slice(0, MAX_FINDINGS);
}
