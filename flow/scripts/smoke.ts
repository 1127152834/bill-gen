/**
 * Live end-to-end smoke run against a running server (real LLM proxy + real Tavily).
 *
 *   LIVE_SMOKE=1 pnpm smoke                       # server on http://127.0.0.1:8797
 *   LIVE_SMOKE=1 SMOKE_BASE_URL=http://127.0.0.1:8800 pnpm smoke
 *
 * Submits one personal and one business job, polls to completion, prints status / R1-R11 / warnings /
 * usage / wall time and saves each result to data/smoke/<label>-<id>.json (plus the progress events).
 * Exits 0 without doing anything unless LIVE_SMOKE=1. No secrets are read or printed here.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const ROOT = resolve(import.meta.dirname, '..');
const OUT_DIR = resolve(ROOT, 'data/smoke');
const BASE_URL = (process.env.SMOKE_BASE_URL ?? 'http://127.0.0.1:8797').replace(/\/+$/, '');
const POLL_MS = 5_000;
const JOB_TIMEOUT_MS = 12 * 60_000;
const REQUEST_TIMEOUT_MS = 30_000;
const RULE_IDS = [
  'R1_count_in_range',
  'R2_balance_continuity',
  'R3_dates_in_period',
  'R4_recurring_consistency',
  'R5_card_payment_match',
  'R6_amount_within_range',
  'R7_geo_consistency',
  'R8_no_duplicates',
  'R9_evidence_present',
  'R10_no_payroll_income_personal',
  'R11_credit_within_limit',
] as const;
const LIMITS = { llmCalls: 40, tavilyQueries: 25 };

type Rule = { ruleId: string; passed: boolean; violations: { transactionIds: string[]; message: string }[] };
type Result = {
  status: string;
  warnings: string[];
  audit: { rules: Rule[]; findings: { severity: string; problem: string }[] };
  usage: { llmCalls: number; tokens: number; tavilyQueries: number };
  statements: { accountType: string; transactions: unknown[] }[];
};
type View = { id: string; status: string; result: Result | { partial: true } | null; error: { code: string; message: string; usage?: { llmCalls: number; tavilyQueries: number } } | null };

const JOBS: { label: string; body: unknown; expectedCounts: Record<string, [number, number]> }[] = [
  {
    label: 'personal',
    body: { subjectType: 'personal', accounts: { checking: { min: 8, max: 13 }, credit: { min: 15, max: 25 } }, months: 1 },
    expectedCounts: { checking: [8, 13], credit: [15, 25] },
  },
  {
    label: 'business',
    body: {
      subjectType: 'business',
      profile: {
        legalName: 'Pecan Street Coffee Co LLC',
        entityType: 'LLC',
        industry: 'Coffee shop / cafe',
        description: 'Independent neighborhood cafe in Austin serving espresso drinks, pastries and light lunch.',
        founded: '2019-03',
        employeeCount: 12,
        address: { street: '1427 E 6th St', city: 'Austin', state: 'TX', zip: '78702' },
      },
      accounts: { checking: { min: 20, max: 30 } },
      months: 1,
    },
    expectedCounts: { business_checking: [20, 30] },
  },
];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function api(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${BASE_URL}${path}`, {
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
}

function isFullResult(r: View['result']): r is Result {
  return r !== null && 'audit' in r;
}

async function fetchEvents(id: string): Promise<unknown[]> {
  try {
    const res = await fetch(`${BASE_URL}/api/generations/${id}/events`, { signal: AbortSignal.timeout(20_000) });
    const text = await res.text();
    const events: unknown[] = [];
    for (const block of text.split('\n\n')) {
      const data = block.split('\n').find((l) => l.startsWith('data:') && !block.includes('event: done'));
      if (data) events.push(JSON.parse(data.slice(5).trim()));
    }
    return events;
  } catch {
    return [];
  }
}

/** Every one of R1-R11 must be present and passed; an empty or truncated `rules` array fails. Exported for a self-check. */
export function checkRules(rules: Rule[]): { ok: boolean; lines: string[] } {
  const lines: string[] = [];
  let ok = true;
  for (const id of RULE_IDS) {
    const rule = rules.find((r) => r.ruleId === id);
    if (!rule) {
      lines.push(`✗ ${id}  (MISSING from audit.rules)`);
      ok = false;
    } else if (!rule.passed) {
      lines.push(`✗ ${id}  (${rule.violations.length} violation(s))`);
      for (const v of rule.violations.slice(0, 5)) lines.push(`    - ${v.message}`);
      ok = false;
    } else {
      lines.push(`✓ ${id}`);
    }
  }
  return { ok, lines };
}

async function runJob(job: (typeof JOBS)[number]): Promise<boolean> {
  const started = Date.now();
  console.log(`\n=== ${job.label} ===`);
  const created = await api('/api/generations', { method: 'POST', body: JSON.stringify(job.body) });
  if (created.status !== 202) {
    console.log(`✗ submit failed: HTTP ${created.status} ${(await created.text()).slice(0, 300)}`);
    return false;
  }
  const { id } = (await created.json()) as { id: string };
  console.log(`id ${id}`);

  let view: View | undefined;
  let lastStatus = '';
  while (Date.now() - started < JOB_TIMEOUT_MS) {
    await sleep(POLL_MS);
    const res = await api(`/api/generations/${id}`);
    if (!res.ok) {
      console.log(`✗ poll failed: HTTP ${res.status}`);
      return false;
    }
    view = (await res.json()) as View;
    if (view.status !== lastStatus) {
      lastStatus = view.status;
      console.log(`[${Math.round((Date.now() - started) / 1000)}s] status ${view.status}`);
    }
    if (['completed', 'completed_with_warnings', 'failed', 'interrupted'].includes(view.status)) break;
  }
  const wallS = Math.round((Date.now() - started) / 1000);
  if (!view || !['completed', 'completed_with_warnings', 'failed', 'interrupted'].includes(view.status)) {
    console.log(`✗ timed out after ${wallS}s (last status ${view?.status ?? 'unknown'})`);
    return false;
  }

  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(resolve(OUT_DIR, `${job.label}-${id}.json`), JSON.stringify(view.result, null, 2));
  const events = await fetchEvents(id);
  writeFileSync(resolve(OUT_DIR, `${job.label}-${id}.events.json`), JSON.stringify(events, null, 2));
  const retries = events.filter((e) => (e as { type?: string }).type === 'retry').length;

  console.log(`status: ${view.status}   wall time: ${wallS}s   retries: ${retries}   events: ${events.length}`);
  let ok = view.status === 'completed' || view.status === 'completed_with_warnings';
  if (view.error) {
    const u = view.error.usage;
    console.log(`error: ${view.error.code} ${view.error.message}${u ? ` (usage: llmCalls=${u.llmCalls} tavilyQueries=${u.tavilyQueries})` : ''}`);
  }
  if (!isFullResult(view.result)) {
    console.log('✗ no full result stored');
    return false;
  }
  const r = view.result;

  const gate = checkRules(r.audit.rules);
  for (const line of gate.lines) console.log(line);
  if (!gate.ok) ok = false;
  for (const [type, [min, max]] of Object.entries(job.expectedCounts)) {
    const st = r.statements.find((s) => s.accountType === type);
    const n = st?.transactions.length ?? -1;
    const good = n >= min && n <= max;
    console.log(`${good ? '✓' : '✗'} ${type} transactions: ${n} (expected ${min}-${max})`);
    if (!good) ok = false;
  }
  console.log(`warnings (${r.warnings.length}):`);
  for (const w of r.warnings) console.log(`  - ${w}`);
  console.log(`audit findings: ${r.audit.findings.length}`);
  for (const f of r.audit.findings) console.log(`  - [${f.severity}] ${f.problem}`);
  console.log(`usage: llmCalls=${r.usage.llmCalls} tokens=${r.usage.tokens} tavilyQueries=${r.usage.tavilyQueries}`);
  for (const [k, limit] of Object.entries(LIMITS) as [keyof typeof LIMITS, number][]) {
    if (r.usage[k] > limit) {
      console.log(`✗ ${k} ${r.usage[k]} exceeds ${limit}`);
      ok = false;
    }
  }
  console.log(`${ok ? 'PASS' : 'FAIL'} ${job.label}`);
  return ok;
}

async function main(): Promise<void> {
  if (process.env.LIVE_SMOKE !== '1') {
    console.log('smoke: skipped (set LIVE_SMOKE=1 to run the live end-to-end check against a running server)');
    return;
  }
  console.log(`smoke: server ${BASE_URL}`);
  const results: boolean[] = [];
  for (const job of JOBS) results.push(await runJob(job));
  console.log(`\nsmoke: ${results.every(Boolean) ? 'all jobs passed' : 'FAILED'}`);
  if (!results.every(Boolean)) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((err) => {
    console.error(`smoke: crashed: ${err instanceof Error ? err.message : 'unknown error'}`);
    process.exitCode = 1;
  });
}
