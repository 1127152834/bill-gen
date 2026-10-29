/**
 * 把 `ProgressEvent` 摊成一行行能直接读的控制台日志。
 *
 * 生成一次账单是「跑完 9 个节点 + N 次检索 + 若干次重试」的黑盒，出问题时最难回答的三个问题都是
 * 「现在跑到哪了、这一步花了多久、为什么退回去了」。SSE 事件进浏览器之前先把它们按人读的格式
 * 打到 stdout，跑命令行/看服务端日志就能跟着流程走，不必等前端。
 *
 * 输出形如（时间来自注入的 `now`，便于测试）：
 *
 *   12:03:41.512 +0.0s  ▶ research          (8 查询)
 *   12:03:44.101 +2.6s    · 检索 2/8 完成：apartments for rent Austin TX → 5 源
 *   12:03:47.993 +6.5s  ✔ research          6.5s
 *   12:03:48.001 +6.5s  ! 警告：category utilities dropped: search failed
 */
import type { ProgressEvent } from './deps.js';

/** 一行日志的组成；给 `formatEvent` 之外的调用方（测试、别的前端）复用。 */
export type LogLine = {
  /** `HH:MM:SS.mmm` 本地时间。 */
  clock: string;
  /** 距日志创建（一般=任务开始）的秒数，保留一位小数。 */
  elapsed: string;
  /** 单字符前缀：`▶` 开始 / `✔` 完成 / `·` 步骤 / `!` 警告 / `↻` 重试 / `✓` 规则通过 / `✗` 规则失败。 */
  mark: string;
  /** 事件主体，节点名或消息。 */
  text: string;
  /** 可选的尾部补充（耗时、来源数）。 */
  note: string;
};

const MARK_BY_TYPE: Record<ProgressEvent['type'], string> = {
  node_start: '▶',
  node_end: '✔',
  step: '·',
  research: '·',
  rules: '✓',
  retry: '↻',
  warning: '!',
  progress: '·',
};

/** 节点名统一成定宽，日志才能按列扫。 */
const NODE_WIDTH = 13;

const clock = (ms: number): string => {
  const d = new Date(ms);
  const pad = (n: number, w = 2) => String(n).padStart(w, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
};

const seconds = (ms: number): string => `${(ms / 1000).toFixed(1)}s`;

/** `(N 查询)` / `(N 源)` 这种可选尾巴。 */
const note = (parts: (string | undefined)[]): string => parts.filter((p): p is string => !!p).join('  ');

/** 一条事件 → 一行日志的四段内容（不拼字符串，方便换前端或测试断言）。 */
export function formatEvent(event: ProgressEvent, opts: { startedAt: number; now: number }): LogLine {
  const elapsed = seconds(Math.max(0, opts.now - opts.startedAt));
  const mark = MARK_BY_TYPE[event.type] ?? '·';
  switch (event.type) {
    case 'node_start':
      return {
        clock: clock(opts.now),
        elapsed,
        mark,
        text: event.node.padEnd(NODE_WIDTH),
        note: note([event.detail ? `(${event.detail})` : undefined]),
      };
    case 'node_end':
      return {
        clock: clock(opts.now),
        elapsed,
        mark: '✔',
        text: event.node.padEnd(NODE_WIDTH),
        note: note([event.ms !== undefined ? seconds(event.ms) : undefined, event.detail]),
      };
    case 'step':
      return {
        clock: clock(opts.now),
        elapsed,
        mark,
        text: `${event.node.padEnd(NODE_WIDTH)} ${event.message}`,
        note: '',
      };
    case 'research':
      return {
        clock: clock(opts.now),
        elapsed,
        mark,
        text: `检索 ${event.query}`,
        note: note([`${event.sources.length} 源`]),
      };
    case 'rules': {
      const passed = event.results.filter((r) => r.violations.length === 0).length;
      const failed = event.results
        .filter((r) => r.violations.length > 0)
        .map((r) => `${r.ruleId}×${r.violations.length}`);
      return {
        clock: clock(opts.now),
        elapsed,
        mark: failed.length === 0 ? '✓' : '✗',
        text: `规则 ${passed}/${event.results.length} 通过`,
        note: note([failed.length > 0 ? `未过：${failed.join(' ')}` : undefined]),
      };
    }
    case 'retry':
      return {
        clock: clock(opts.now),
        elapsed,
        mark,
        text: `退回 ${event.target}`,
        note: note([event.reason]),
      };
    case 'warning':
      return { clock: clock(opts.now), elapsed, mark, text: `警告：${event.message}`, note: '' };
    case 'progress':
      return { clock: clock(opts.now), elapsed, mark, text: event.message, note: '' };
  }
}

/** 一行纯文本（终端用；宽字符不影响对齐，因为只对齐了节点名）。 */
export function formatLine(event: ProgressEvent, opts: { startedAt: number; now: number }): string {
  const line = formatEvent(event, opts);
  return [`[${line.clock}] ${line.elapsed.padStart(6)} ${line.mark} ${line.text}`, line.note]
    .filter(Boolean)
    .join('  ')
    .trimEnd();
}

export type ConsoleLoggerOptions = {
  /** 写一行；默认 `console.log`。 */
  write?: (line: string) => void;
  /** 只要这些节点的事件（不给就全打；research 的逐条检索结果可以用 `quietResearch` 单关）。 */
  nodes?: string[];
  /** `true` 时不打每次检索的明细，只打开始/结束与警告。 */
  quietResearch?: boolean;
  /** 注入时钟，测试用。 */
  now?: () => number;
};

/**
 * 造一个「进度事件 → 控制台」的接收器，直接当 `runGeneration({ onEvent })` 用。
 * 创建时刻就是计时零点，所以日志里的 `+Xs` 是对整条生成流程说的，不是对单节点。
 */
export function createConsoleLogger(opts: ConsoleLoggerOptions = {}) {
  const now = opts.now ?? Date.now;
  const startedAt = now();
  const write = opts.write ?? ((line: string) => console.log(line));
  const only = opts.nodes && opts.nodes.length > 0 ? new Set(opts.nodes) : undefined;

  return (event: ProgressEvent): void => {
    if (only && 'node' in event && !only.has(event.node)) return;
    if (opts.quietResearch && event.type === 'research') return;
    write(formatLine(event, { startedAt, now: now() }));
  };
}

/**
 * 两个接收器串起来：SSE/状态库照旧收事件，控制台额外打一份。
 * `runGeneration({ onEvent: tee(onEvent, createConsoleLogger()) })`。
 */
export function tee(
  ...sinks: ((event: ProgressEvent) => void)[]
): (event: ProgressEvent) => void {
  return (event) => {
    for (const sink of sinks) {
      try {
        sink(event);
      } catch {
        // 日志永远不该把任务带崩
      }
    }
  };
}
