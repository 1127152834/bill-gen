import { describe, expect, it } from 'vitest';
import { createConsoleLogger, formatEvent, formatLine, tee } from '../src/graph/logfmt.js';
import type { ProgressEvent } from '../src/graph/deps.js';

const at = (ms: number) => new Date(ms).getTime();
const START = at(0);

describe('formatEvent', () => {
  it('给开始/结束加上耗时与定宽节点名', () => {
    const start = formatEvent({ type: 'node_start', node: 'research', detail: '8 条查询' }, { startedAt: START, now: 1500 });
    expect(start.mark).toBe('▶');
    expect(start.text.trim()).toBe('research');
    expect(start.note).toBe('(8 条查询)');

    const end = formatEvent({ type: 'node_end', node: 'research', ms: 6500 }, { startedAt: START, now: 8000 });
    expect(end.mark).toBe('✔');
    expect(end.note).toBe('6.5s');
    expect(end.elapsed).toBe('8.0s');
  });

  it('step 事件带节点名，方便扫日志时知道是谁在说话', () => {
    const line = formatEvent({ type: 'step', node: 'billgen', message: '草稿未通过校验（2 项）' }, { startedAt: START, now: 2000 });
    expect(line.mark).toBe('·');
    expect(line.text).toContain('billgen');
    expect(line.text).toContain('草稿未通过校验');
  });

  it('规则事件汇总通过数与未过的规则', () => {
    const line = formatLine(
      {
        type: 'rules',
        results: [
          { ruleId: 'R3', violations: [] },
          { ruleId: 'R5', violations: [{ message: 'a' }, { message: 'b' }] },
        ] as never,
      },
      { startedAt: START, now: 3000 },
    );
    expect(line).toContain('规则 1/2 通过');
    expect(line).toContain('R5×2');
  });

  it('失败节点也有结束行（detail=失败）', () => {
    const line = formatLine({ type: 'node_end', node: 'validate', ms: 12, detail: '失败' }, { startedAt: START, now: 1000 });
    expect(line).toContain('validate');
    expect(line).toContain('失败');
  });
});

describe('createConsoleLogger / tee', () => {
  it('按节点过滤与 quietResearch 生效', () => {
    const lines: string[] = [];
    const log = createConsoleLogger({
      write: (l) => lines.push(l),
      nodes: ['research'],
      quietResearch: true,
      now: () => 0,
    });
    log({ type: 'node_start', node: 'validate' });
    log({ type: 'research', query: 'q', sources: [] });
    log({ type: 'node_start', node: 'research', detail: '3 条查询' });
    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain('research');
  });

  it('tee 把事件同时给两个接收器，且吞掉日志端异常', () => {
    const a: ProgressEvent[] = [];
    const out: string[] = [];
    const sink = tee(
      (e) => a.push(e),
      () => {
        throw new Error('disk full');
      },
      createConsoleLogger({ write: (l) => out.push(l), now: () => 0 }),
    );
    expect(() => sink({ type: 'warning', message: 'boom' })).not.toThrow();
    expect(a).toHaveLength(1);
    expect(out[0]).toContain('boom');
  });
});
