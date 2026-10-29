export type BudgetLimits = { llmCalls: number; tavilyQueries: number; ms: number };

export const DEFAULT_BUDGET_LIMITS: BudgetLimits = { llmCalls: 40, tavilyQueries: 25, ms: 600_000 };

export class BudgetExceededError extends Error {
  readonly code = 'BUDGET_EXCEEDED';
  readonly resource: keyof BudgetLimits;
  constructor(resource: keyof BudgetLimits, limit: number) {
    super(`Task budget exceeded: ${resource} limit of ${limit} reached`);
    this.name = 'BudgetExceededError';
    this.resource = resource;
  }
}

/**
 * Per-task resource budget: model calls, Tavily queries and wall-clock time.
 * The clock starts when the Budget is constructed.
 */
export class Budget {
  private readonly limits: BudgetLimits;
  private readonly startedAt: number;
  private llmCalls = 0;
  private tokens = 0;
  private tavilyQueries = 0;

  constructor(
    limits: Partial<BudgetLimits> = {},
    private readonly now: () => number = Date.now,
  ) {
    this.limits = { ...DEFAULT_BUDGET_LIMITS, ...limits };
    this.startedAt = now();
  }

  /** Count one real model attempt; throws before the attempt when over budget. */
  chargeLlm(tokens = 0): void {
    this.checkTime();
    if (this.llmCalls >= this.limits.llmCalls) throw new BudgetExceededError('llmCalls', this.limits.llmCalls);
    this.llmCalls += 1;
    this.tokens += tokens;
  }

  /** Record token usage learned after a call was already charged. */
  addTokens(tokens: number): void {
    this.tokens += tokens;
  }

  chargeTavily(): void {
    this.checkTime();
    if (this.tavilyQueries >= this.limits.tavilyQueries) {
      throw new BudgetExceededError('tavilyQueries', this.limits.tavilyQueries);
    }
    this.tavilyQueries += 1;
  }

  /** What is left before each limit is reached (never negative). */
  remaining(): { llmCalls: number; tavilyQueries: number } {
    return {
      llmCalls: Math.max(0, this.limits.llmCalls - this.llmCalls),
      tavilyQueries: Math.max(0, this.limits.tavilyQueries - this.tavilyQueries),
    };
  }

  usage(): { llmCalls: number; tokens: number; tavilyQueries: number } {
    return { llmCalls: this.llmCalls, tokens: this.tokens, tavilyQueries: this.tavilyQueries };
  }

  private checkTime(): void {
    if (this.now() - this.startedAt > this.limits.ms) throw new BudgetExceededError('ms', this.limits.ms);
  }
}
