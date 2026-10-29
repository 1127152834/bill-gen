import { randomUUID } from 'node:crypto';
import type { BaseCheckpointSaver } from '@langchain/langgraph';
import { SqliteSaver } from '@langchain/langgraph-checkpoint-sqlite';
import {
  GenerationRequestSchema,
  createRng,
  randomPersona,
  runGeneration,
  type GenerationRequest,
  type GraphDeps,
} from '@bh/core';
import {
  finishGeneration,
  getGeneration,
  listQueuedIds,
  markRunningInterrupted,
  saveRequest,
  setStatus,
  type Db,
  type JobError,
} from './db.js';
import type { EventBus } from './events.js';

export interface JobRunner {
  /** Queue a `queued` job; it starts when a worker slot is free. */
  enqueue(id: string): void;
  /** Queue an `interrupted` job to continue from its checkpoint. */
  resume(id: string): void;
  /**
   * Call once at startup, before accepting requests: `running` jobs lost their worker and become
   * `interrupted` (resume continues them); `queued` jobs never started and are queued again.
   */
  recoverOnStartup(): void;
  /** Removes the LangGraph checkpoints of a job (thread = generation id). Safe when there are none. */
  deleteCheckpoints(id: string): Promise<void>;
}

export type JobRunnerOptions = {
  db: Db;
  /** Fresh dependencies for every job: each job has its own `Budget` and model client. */
  makeDeps: () => GraphDeps;
  concurrency: number;
  bus: EventBus;
  /** Defaults to a `SqliteSaver` on `db`, so checkpoints live in the same database file. */
  checkpointer?: BaseCheckpointSaver;
  /** Secret values scrubbed from stored error messages. */
  secrets?: string[];
};

const MAX_MESSAGE = 300;
const GENERIC_FAILURE: JobError = { code: 'GENERATION_FAILED', message: 'Generation failed' };
/**
 * Core error codes shown as they are, with the error's own (scrubbed) message, or `null` for a
 * fixed message when the error's message can quote model-written text. Anything else is generic.
 */
const KNOWN_CODES: Record<string, string | null> = {
  BUDGET_EXCEEDED: null,
  LLM_CALL_FAILED: null,
  LLM_OUTPUT_INVALID: null,
  INFEASIBLE_REQUEST: null,
  SCHEDULE_INFEASIBLE: null,
  LIFE_MODEL_INVALID: 'The life model failed the code checks after every retry',
};

/** Model calls, tokens and Tavily queries a job used; never throws. */
function usageOf(deps: GraphDeps): JobError['usage'] {
  try {
    return deps.budget.usage();
  } catch {
    return undefined;
  }
}

export function createJobRunner(opts: JobRunnerOptions): JobRunner {
  const { db, bus } = opts;
  const checkpointer = opts.checkpointer ?? new SqliteSaver(db);
  const secrets = (opts.secrets ?? []).filter((s) => s !== '');
  const queue: { id: string; resume: boolean }[] = [];
  let active = 0;

  const scrub = (text: string): string => {
    let out = text;
    for (const secret of secrets) out = out.split(secret).join('***');
    return out.replace(/\s+/g, ' ').trim().slice(0, MAX_MESSAGE);
  };

  /** Known core codes (`KNOWN_CODES`) keep their code and a scrubbed or fixed message; anything else is generic. */
  function toJobError(err: unknown): JobError {
    const code = (err as { code?: unknown } | null)?.code;
    if (typeof code !== 'string' || !Object.hasOwn(KNOWN_CODES, code) || !(err instanceof Error)) return { ...GENERIC_FAILURE };
    const fixed = KNOWN_CODES[code];
    return { code, message: fixed ?? (scrub(err.message) || GENERIC_FAILURE.message) };
  }

  /** The statements checkpointed before the failure, if the pipeline got that far. */
  async function partialResult(id: string): Promise<unknown> {
    try {
      const tuple = await checkpointer.getTuple({ configurable: { thread_id: id } });
      const statements = (tuple?.checkpoint.channel_values as { statements?: unknown } | undefined)?.statements;
      return Array.isArray(statements) && statements.length > 0 ? { partial: true, statements } : undefined;
    } catch {
      return undefined;
    }
  }

  /** A stored request without a profile gets a random persona (charged to the job budget); seed and profile are saved back. */
  async function completeRequest(id: string, deps: GraphDeps): Promise<GenerationRequest> {
    const row = getGeneration(db, id);
    if (!row) throw new Error(`generation ${id} not found`);
    let request = GenerationRequestSchema.parse(JSON.parse(row.request_json));
    if (request.profile && request.seed) return request;
    const seed = request.seed ?? randomUUID();
    const profile =
      request.profile ??
      (await randomPersona(request.subjectType, deps.personaDefaults, { llm: deps.llm, rng: createRng(seed).fork('persona') }));
    request = { ...request, seed, profile };
    saveRequest(db, id, request);
    return request;
  }

  async function execute(id: string, resume: boolean): Promise<void> {
    setStatus(db, id, 'running');
    let deps: GraphDeps | undefined;
    try {
      deps = opts.makeDeps();
      const request = await completeRequest(id, deps);
      const result = await runGeneration(request, deps, {
        threadId: id,
        checkpointer,
        resume,
        onEvent: (e) => bus.emit(id, e),
      });
      finishGeneration(db, id, { status: result.status, result });
      bus.finish(id, result.status);
    } catch (err) {
      const partial = await partialResult(id);
      const error: JobError = { ...toJobError(err), ...(deps && { usage: usageOf(deps) }) };
      finishGeneration(db, id, { status: 'failed', error, ...(partial !== undefined && { result: partial }) });
      bus.finish(id, 'failed');
    }
  }

  function pump(): void {
    while (active < opts.concurrency && queue.length > 0) {
      const job = queue.shift()!;
      active++;
      execute(job.id, job.resume)
        .catch(() => {
          // execute records its own failures; a storage error here must not crash the process
        })
        .finally(() => {
          active--;
          pump();
        });
    }
  }

  return {
    enqueue(id) {
      queue.push({ id, resume: false });
      pump();
    },
    resume(id) {
      setStatus(db, id, 'queued');
      queue.push({ id, resume: true });
      pump();
    },
    recoverOnStartup() {
      markRunningInterrupted(db);
      for (const id of listQueuedIds(db)) queue.push({ id, resume: false });
      pump();
    },
    async deleteCheckpoints(id) {
      // SqliteSaver creates its tables on first use; a read makes sure they exist, so a job that never ran can be deleted.
      await checkpointer.getTuple({ configurable: { thread_id: id } });
      await checkpointer.deleteThread(id);
    },
  };
}
