import { describe, expect, it } from 'vitest';
import { createRng } from '../src/index.js';

const seq = (r: ReturnType<typeof createRng>, n = 20) => Array.from({ length: n }, () => r.float());

describe('createRng', () => {
  it('same seed yields identical sequence; different seed differs', () => {
    expect(seq(createRng('seed-1'))).toEqual(seq(createRng('seed-1')));
    expect(seq(createRng('seed-1'))).not.toEqual(seq(createRng('seed-2')));
  });
  it('float is in [0, 1)', () => {
    const r = createRng('f');
    for (let i = 0; i < 1000; i++) {
      const x = r.float();
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThan(1);
    }
  });
  it('int(3,5) is inclusive and hits every value', () => {
    const r = createRng('ints');
    const seen = new Set<number>();
    for (let i = 0; i < 10000; i++) seen.add(r.int(3, 5));
    expect([...seen].sort()).toEqual([3, 4, 5]);
  });
  it('chance respects 0 and 1', () => {
    const r = createRng('c');
    for (let i = 0; i < 100; i++) {
      expect(r.chance(0)).toBe(false);
      expect(r.chance(1)).toBe(true);
    }
  });
  it('pick returns members and throws on empty', () => {
    const r = createRng('p');
    for (let i = 0; i < 50; i++) expect(['a', 'b', 'c']).toContain(r.pick(['a', 'b', 'c']));
    expect(() => r.pick([])).toThrow();
  });
  it('weighted follows weights and never picks zero-weight items', () => {
    const r = createRng('w');
    const counts = { a: 0, b: 0, z: 0 };
    for (let i = 0; i < 10000; i++) {
      counts[r.weighted([{ item: 'a' as const, weight: 3 }, { item: 'b' as const, weight: 1 }, { item: 'z' as const, weight: 0 }])]++;
    }
    expect(counts.z).toBe(0);
    expect(counts.a / counts.b).toBeGreaterThan(2.5);
    expect(counts.a / counts.b).toBeLessThan(3.5);
  });
  it('weighted rejects negative, NaN, infinite and all-zero weights', () => {
    const r = createRng('w2');
    expect(() => r.weighted([{ item: 'a', weight: 1 }, { item: 'b', weight: 1 }, { item: 'c', weight: -1 }])).toThrow();
    expect(() => r.weighted([{ item: 'a', weight: 1 }, { item: 'b', weight: NaN }])).toThrow();
    expect(() => r.weighted([{ item: 'a', weight: 1 }, { item: 'b', weight: Infinity }])).toThrow();
    expect(() => r.weighted([{ item: 'a', weight: 0 }, { item: 'b', weight: 0 }])).toThrow();
    expect(() => r.weighted([])).toThrow();
  });
  it('fork streams are deterministic, differ by label, and do not disturb the parent', () => {
    const a1 = seq(createRng('s').fork('a'));
    expect(a1).toEqual(seq(createRng('s').fork('a')));
    expect(a1).not.toEqual(seq(createRng('s').fork('b')));
    const p1 = createRng('s');
    const p2 = createRng('s');
    p2.fork('a').float();
    expect(seq(p1)).toEqual(seq(p2));
  });
});
