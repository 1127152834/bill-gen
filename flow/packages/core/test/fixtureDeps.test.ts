import { describe, expect, it } from 'vitest';
import { createRng, randomPersona } from '../src/index.js';
import { createFixtureDeps } from '../src/testing/index.js';

describe('fixture deps persona', () => {
  it('keeps the fake persona in Austin whatever metro the rng would pick', async () => {
    for (let i = 0; i < 40; i++) {
      const deps = createFixtureDeps();
      const profile = await randomPersona('personal', deps.personaDefaults, { llm: deps.llm, rng: createRng(`seed-${i}`).fork('persona') });
      expect(profile.address.city, `seed-${i}`).toBe('Austin');
      expect(profile.address.state).toBe('TX');
    }
  });
});
