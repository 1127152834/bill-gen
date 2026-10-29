/** Seeded PRNG: xmur3 string hash feeding mulberry32. Deterministic across runs. */

export interface Rng {
  /** Uniform in [0, 1). */
  float(): number;
  /** Uniform integer in [min, max], both inclusive. */
  int(min: number, max: number): number;
  chance(p: number): boolean;
  pick<T>(xs: readonly T[]): T;
  weighted<T>(xs: readonly { item: T; weight: number }[]): T;
  /** Independent child stream seeded by `seed + '/' + label`. */
  fork(label: string): Rng;
}

function xmur3(str: string): () => number {
  let h = 1779033703 ^ str.length;
  for (let i = 0; i < str.length; i++) {
    h = Math.imul(h ^ str.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  return () => {
    h = Math.imul(h ^ (h >>> 16), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    return (h ^= h >>> 16) >>> 0;
  };
}

function mulberry32(a: number): () => number {
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function createRng(seed: string): Rng {
  const next = mulberry32(xmur3(seed)());
  const rng: Rng = {
    float: next,
    int(min, max) {
      return min + Math.floor(next() * (max - min + 1));
    },
    chance(p) {
      return next() < p;
    },
    pick(xs) {
      if (xs.length === 0) throw new Error('pick: empty array');
      return xs[Math.floor(next() * xs.length)] as (typeof xs)[number];
    },
    weighted(xs) {
      for (const x of xs) {
        if (!Number.isFinite(x.weight) || x.weight < 0) {
          throw new Error(`weighted: weight must be finite and >= 0, got ${x.weight}`);
        }
      }
      const total = xs.reduce((s, x) => s + x.weight, 0);
      if (!(total > 0)) throw new Error('weighted: total weight must be positive');
      let r = next() * total;
      for (const x of xs) {
        if (x.weight <= 0) continue;
        if (r < x.weight) return x.item;
        r -= x.weight;
      }
      // Floating-point leftover: fall back to the last positive-weight item.
      return [...xs].reverse().find((x) => x.weight > 0)!.item;
    },
    fork(label) {
      return createRng(`${seed}/${label}`);
    },
  };
  return rng;
}
