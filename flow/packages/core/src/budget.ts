export type BudgetLimits = { llmCalls: number; tavilyQueries: number; ms: number };

/**
 * 每题预算上限。Tavily 额度是「软」的：见 `tryChargeTavily`——用超了只降级（少检索几个品类），
 * 不让整个任务失败。llmCalls 也是软上限的一半要靠重试留足余量，所以给得比历史值宽。
 */
export const DEFAULT_BUDGET_LIMITS: BudgetLimits = { llmCalls: 60, tavilyQueries: 60, ms: 900_000 };

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
    if (!this.tryChargeTavily()) throw new BudgetExceededError('tavilyQueries', this.limits.tavilyQueries);
  }

  /**
   * 软上限：额度够就记一次并返回 true，额度用完返回 false（调用方跳过这次检索、继续跑）。
   * 只有时间上限仍然硬失败——它防的是任务挂死，不是省钱。
   */
  tryChargeTavily(): boolean {
    this.checkTime();
    if (this.tavilyQueries >= this.limits.tavilyQueries) return false;
    this.tavilyQueries += 1;
    return true;
  }

  /** 还需要几次检索才到上限（给「要不要再重试一次」这类判断用）。 */
  get tavilyLeft(): number {
    return Math.max(0, this.limits.tavilyQueries - this.tavilyQueries);
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
