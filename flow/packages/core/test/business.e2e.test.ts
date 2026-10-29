import { describe, expect, it } from 'vitest';
import { createRng, randomPersona, type BusinessProfile } from '../src/index.js';
import { createFixtureDeps, fixtureRequest } from '../src/testing/index.js';

/**
 * 企业主体的**人格**链路：离线 fixture 下 `subjectType: 'business'` 必须能随机出合格的企业 persona。
 *
 * 之前 `persona` 节点只备了个人 fixture，企业请求在 persona 解析处就整单失败——线上表现是
 * `GENERATION_FAILED`、`llmCalls: 1`、容器里连一行进度日志都没有。
 *
 * 注意范围：这里只验人格。整单出企业账单还要求 research / lifeModel 的 fixture 也按企业口径
 * 备料（离线 fixture 的检索分类与账本都是个人口径，企业请求会卡在 lifeModel 的 factId 校验），
 * 企业**版式**的端到端对比走 `tools/e2e_compare.py --as-business`。
 */
describe('business persona', () => {
  it('randomises a valid business profile', async () => {
    const deps = createFixtureDeps();
    const profile = (await randomPersona('business', undefined, {
      llm: deps.llm,
      rng: createRng('biz-persona'),
    })) as BusinessProfile;

    expect(profile.legalName.length).toBeGreaterThan(0);
    expect(['LLC', 'S-Corp', 'Sole Prop']).toContain(profile.entityType);
    expect(profile.industry.length).toBeGreaterThan(0);
    expect(profile.address.state).toMatch(/^[A-Z]{2}$/);
    expect(profile.employeeCount).toBeGreaterThanOrEqual(0);
  });

  it('keeps a personal request on the personal profile', async () => {
    const deps = createFixtureDeps();
    const profile = (await randomPersona('personal', fixtureRequest().profile, {
      llm: deps.llm,
      rng: createRng('personal-persona'),
    })) as { name?: string };
    expect(profile.name).toBe('Maya Okafor');
  });
});
