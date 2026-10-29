# SDD ledger — plan: docs/superpowers/plans/2026-10-29-bill-hunter-statement-generator.md

Spec: docs/superpowers/specs/2026-10-29-bill-hunter-statement-generator-design.md
Branch: feat/statement-generator (from main @ 50f7f1d); setup commit 5c14937 (.gitignore .env). `.env` written by controller (LLM key from user, API_KEY generated). TAVILY_API_KEY not yet provided.

## Pre-flight scan

| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1 self | git init, .gitignore | Repo already initialized by controller; .gitignore already has `.superpowers/`, `.env` → R1 |
| T2→T5/T6/T7 | FinancialModel discretionary `pricing:'fuel'` vs T5 `fuelAmount(pricePerGallonCents)`; `schedule(model,norm,rng)` gets no facts | No source for per-gallon price → R2 |
| T2→T15/16 | web imports `@bh/core` schemas | core main entry pulls langgraph/openai into browser → R3 |
| T4→T6/T7 | single `periodStart/periodEnd`; checking=months, credit=closing-day cycles | ambiguous with two cycle sets → R4 |
| T5/T6 vs T7 R3 | property test allows postedDate ≤ periodEnd+3; Global: count by postedDate in cycle | txns posting after last cycle belong to no statement → R5 |
| T6 self | property ranges min∈[3,30] vs fixtures with many priority≤2 items | test would hit ScheduleInfeasibleError legitimately → R6 |
| T9→T12→T13/T16 | fakeLlm in test/helpers; T13 LLM_FAKE and T16 need fixtures from core tests | server/web can't import core test dir → R7 |
| T11 vs T12 | T11 nodes return Partial<GraphState>, GraphState defined in T12 | ordering conflict → R8 |
| T5 self | taxable amounts; fuel pump prices already include tax | → R2 |
| T3,T4,T7,T8,T10,T13,T14,T17 self | tests vs code vs files | consistent |
| T7→T12 | runRules/assembleStatements signatures | consistent |
| T10→T13 | ResearchCache interface → SQLite impl | consistent |

## Rulings

- R1 Ruling: Task 1 skips `git init` and appends to the existing .gitignore; does not overwrite `.env` other than LLM_BASE_URL/LLM_STRUCTURED_MODE from probe — controller already did setup — cost if wrong: none.
- R2 Ruling: Task 2 adds optional `unitPriceCents?: number` to discretionaryPool items, required (refine) when `pricing==='fuel'` (per-gallon price set by lifeModel from the fact). Fuel: amount = unitPrice × gallons(8.000–16.000), never sales-taxed (pump prices include tax). R6 for fuel checks [unitPrice×8 − 100, unitPrice×16 + 100] — US pump prices are tax-inclusive — cost if wrong: small schema change.
- R3 Ruling: `@bh/core` package.json `exports` has `.` (everything), `./schemas` (zod schemas + types only, browser-safe), `./testing` (fake LLM + fixture deps). Web imports only `@bh/core/schemas` — keeps langgraph out of the browser bundle — cost if wrong: re-export tweak.
- R4 Ruling: NormalizedRequest.periodStart = earliest cycle start, periodEnd = latest cycle end across all requested accounts — cost if wrong: minor.
- R5 Ruling: schedule drops any txn whose postedDate falls outside every cycle of its account (it would belong to another statement); R3 checks postedDate inside the account's cycles and transactionDate ∈ [postedDate−5, postedDate]; property test asserts the same instead of "periodEnd+3" — spec says 日期都在账单周期内 — cost if wrong: a few edge-of-month txns missing.
- R6 Ruling: Task 6 property test accepts `ScheduleInfeasibleError` only when some cycle's count of priority≤2 items exceeds that account's max; otherwise invariants must hold — matches plan's own infeasibility rule — cost if wrong: none.
- R7 Ruling: fake LLM lives at `packages/core/src/testing/fakeLlm.ts` (Task 9); Task 12 adds `packages/core/src/testing/fixtureDeps.ts` (`createFixtureDeps(): GraphDeps`-compatible pieces) with fixtures in `packages/core/fixtures/`; exported via `@bh/core/testing` — needed by server LLM_FAKE and web tests — cost if wrong: file moves.
- R8 Ruling: Task 11 node functions take explicit typed inputs and return their output value (e.g. `planResearchNode(input:{norm, profile}, deps): Promise<ResearchQuery[]>`); Task 12 wraps them into graph nodes — avoids forward dependency on GraphState — cost if wrong: thin wrappers.

## Progress
Task 1: dispatched (base 5c14937, implementer sonnet)
Task 1: implemented 00f4cfa (DONE_WITH_CONCERNS minor: smoke script placeholder, server dev placeholder, TS 7.0.2, pnpm allowBuilds esbuild). Probe: baseURL https://codex.aoeen.com/v1, json_schema + json_mode OK, function_calling FAILS on proxy. Under review.
Task 1: Ruling: proxy hostname in docs/llm-probe.md stays — a hostname is not a credential and the brief asks for it — cost if wrong: one doc edit.
Task 1: minor (deferred): apps/web tsconfig doesn't extend base (no explicit strict/noUncheckedIndexedAccess) — carry to Task 15
Task 1: minor (deferred): @types/node ^24 in apps/web vs ^26 elsewhere
Task 1: minor (deferred): Vite demo content (App.css, hero.png, svgs) still in apps/web — remove in Task 15
Task 1: minor (deferred): probe pure functions untested
Task 1: complete (commits 5c14937..00f4cfa, review clean)
Task 2: dispatched (base 00f4cfa, implementer sonnet)
Task 2: implemented 34330ea (DONE; peakMonths 1..12, seasonality[0]=Jan, apr fraction). Under review.
Task 2: minor (deferred): DateStringSchema accepts impossible dates (2026-02-31) — carried into Task 3 (add calendar-validity refine)
Task 2: minor (deferred): RuleId test only asserts length 10
Task 2: minor (deferred): untested refines (centsRange/perMonthRange min>max, seasonal min≤max, taxDates/biweekly dayRule, monthly/irregular cadence, ATM)
Task 2: minor (deferred): min≤max refine duplicated across request.ts/model.ts
Task 2: minor (deferred): Statement doesn't tie accountType to checking/credit block presence — validator/finalize should enforce
Task 2: complete (commits 00f4cfa..34330ea, review clean)
Task 3: dispatched (base 34330ea, implementer sonnet)
Task 3: implemented 725ab84 (DONE). Under review.
Task 3: minor (deferred): garbled comment calendar.test.ts:396-399
Task 3: minor (deferred): DC Emancipation Day not modeled for IRS Apr deadline (2028-04-17 vs real 04-18) — spec rule followed
Task 3: minor (deferred): regex+refine double issue on DateStringSchema
Task 3: minor (deferred): rng.int no arg checks; Date.UTC years <100
Task 3: minor (deferred): fork label collision fork('a').fork('b') == fork('a/b')
Task 3: fix round 1/5 dispatched — Important: weighted() mishandles negative weights
Task 3: fix round 1/5 (1 addressed, 0 open; commits 725ab84..d569cad)
Task 3: minor (deferred): weighted() total not checked for finiteness (overflow of huge weights)
Task 3: complete (commits 34330ea..d569cad, review clean)
Task 4: dispatched (base d569cad, implementer sonnet)
Task 4: implemented 028c7c5 (DONE_WITH_CONCERNS: city tax table is Tax Foundation Midyear 2021 (latest city edition), state avgs Midyear 2026; colTier/populations approximate; no-profile location = pickWeightedMetro(fork('location'))). Under review.
Task 4: Ruling: accept 2021 city-level combined rates (latest published city edition) — the three pinned test cities match; local rates change rarely — cost if wrong: a few cities off by ≤0.5pp of tax, refreshable data file.
Task 4: minor (deferred): possibly-off city rates Arlington TX 0.08 / Tampa 0.07 / Kansas City MO 0.0885 — recheck in final review; RateSchema cap 0.2 (could be 0.12); no data-sanity test; lowercase state gives generic Error; metros not sorted
Task 4: complete (commits d569cad..028c7c5, review clean)
Task 5: dispatched (base 028c7c5, implementer sonnet)
Task 5: implemented b05929b (DONE; window filter before roll; REFUND card-style; biweekly start +0/7). Under review.
Task 5: Ruling: amend ruling 6 — for rolling types (ACH/ZELLE/PAYMENT/DEPOSIT/TRANSFER) transactionDate = postedDate = next bank business day (code as built). ACH doesn't settle on weekends, so the effective date shown on real statements is the business day; leaked post-period dates are dropped by Task 6's postedDate-in-cycle rule (R5) — cost if wrong: a test + 2 lines.
Task 5: Ruling: amend ruling 5 — biweekly income may start on the first matching weekday on/after periodStart or one week later (deterministic rng phase), as built. Payout weeks vary between people, so this is more realistic — cost if wrong: one line.
Task 5: minor (deferred): tip 15–22% bounds untested; fixed_menu narrow-range fallback breaks menu endings; float half-cent tax rounding; seasonality month from scheduled date; byDateThenId never returns 0
Task 5: minor → carried to Task 6: ATM in CARD_TYPES (posts 1–2 days) — ATM should post same/next business day
Task 5: complete (commits 028c7c5..b05929b, 2 findings ruled per plan conflict)
Task 6: dispatched (base b05929b, implementer opus)
Task 6: implemented fa29c85 (DONE_WITH_CONCERNS: rideshare ~3% infeasible (weekly payouts); cafe payroll fixture priority 3; checking side card pmt = ACH; one OD fee per negative stretch; last payment dropped on both sides if credit side posts after period). Under review.
Task 6: note → Task 11 lifeModel prompt: payroll/rent/tax recurring must be priority 1–2 (never trimmable).
Task 6: Ruling: card-payment pairing in "both" mode — a credit PAYMENT whose checking-side date precedes the checking period stays credit-only (paid from a pre-period checking month); a checking-side payment dated inside the checking period is KEPT even when its credit side posts after the last credit cycle (credit side dropped, belongs to next statement). Every in-period pair where both sides land in their periods must match. Realism: checking months should show the card payment — cost if wrong: small pairing tweak.
Task 6: Ruling: one overdraft fee per negative episode (ruling F wording) is acceptable — cost if wrong: realism nit.
Task 6: minor (deferred): settleCycle throws with mandatoryCount ≤ max when pool empty (message should say pool problem); Ledger.replace index -1 unchecked; deletionOrder/latestFirst duplicated; module-level swipeDayCache
Task 6: fix round 1/5 dispatched — Important: overdraft makes cycle exceed max with nothing trimmable (ruling B broken); property generator never varies openingBalance; + pairing ruling change
Task 6: Ruling: a cycle already at max with only priority≤2 rows plus an unavoidable overdraft fee is legitimately infeasible (fee counts as priority 1); ScheduleInfeasibleError routes to lifeModel which can raise opening balance — cost if wrong: ~0.4% retries.
Task 6: fix round 1/5 (3 addressed, 0 open; commits fa29c85..cb8aa94)
Task 6: minor (deferred): chargeOverdraft 3rd branch can drop an ATM pair (−2 rows for one fee); ruling (a) pairing has no dedicated test
Task 6: complete (commits b05929b..cb8aa94, review clean)
Task 7: dispatched (base cb8aa94, implementer sonnet)
Task 7: implemented eba01ca (DONE: assembleStatements returns {statements, meta}; runRules ctx {norm, model, facts, meta}; R8 trips on 2/675 real schedules (same-day same-amount repeat purchase); R7 travel 10% trips on tiny samples 1/675; ROLLING_TYPES exported). Under review.
Task 7: Ruling: real lenders/payees as merchants (e.g. TOYOTA FINANCIAL auto loan) are allowed — the constraint bans real BANK branding of the statement itself (bankName, logos, routing/account numbers), not counterparties that appear on real statements — cost if wrong: swap payee names in fixtures/prompts.
Task 7: minor (deferred): two bad-fixture tests use toContain not expectOnlyFails (R2 credit, R3 outside-cycle); R5(a) skips debits before firstStart; R7 lacks min-sample guard; R8 can flag real scheduler same-day repeat purchases (1–2/675 → costs a retry); 'card-payment' literal duplicated (CARD_PAYMENT_KEY exists) + within/inCycle/sum duplicated; r2Balance long; R1 above-max / R7 no-merchant / R5 spare-payment tests missing
Task 7: complete (commits cb8aa94..eba01ca, review clean)
Task 8: dispatched (base eba01ca, implementer sonnet)
Task 8: implemented e26b7b3 (DONE_WITH_CONCERNS: WebFetch blocked, facts from WebSearch summaries + general knowledge; 16 trades, 13 industries). Under review.
Task 8: Ruling: real payee brands (Toyota Motor Credit, Navient, State Farm, GEICO, Duke Energy, Comcast) as example counterparties are allowed — same as Task 7 ruling; only bank-issuer branding is banned — cost if wrong: genericise examples.
Task 8: minor (deferred): seasonality arrays not averaging 1.0 (landscaper .88, tutor .89, boutique 1.07…); "skip some quarters" estimated tax vs priority 1; "≤10 days late without penalty" misleading; tipping 18% floor / round-up share vs engine; apostrophe allowed+dropped; hybrid {STORE}+real-city examples; brand regex closed list; no test that every SKILLS_BY_NODE entry loads; thin source URLs; Uber payout weekday uncertain
Task 8: fix round 1/5 dispatched — Important: OD fee $27 vs code $35; ATM $10 steps vs $20 multiples; weekly/semi-monthly cadences not expressible in schema; SKILLS_BY_NODE readonly vs get(SkillName[])
Task 8: fix round 1/5 (4 addressed, 0 open; commits e26b7b3..d5c651b)
Task 8: minor (deferred): us-calendar-seasonality.md:67 payroll "preceding business day" vs engine next-day roll; industry-cost-structures.md:8-10 mapping heading splits section 1
Task 8: complete (commits eba01ca..d5c651b, review clean)
Task 9: dispatched (base d5c651b, implementer sonnet)
Task 9: implemented 03c12ce (DONE; live json_schema 6.4s + json_mode 3.2s PASS; Budget.addTokens extra; lazy langchain import). Under review.
Task 9: minor (deferred): parseJsonLoose skips unbalanced first opener (truncated output may yield inner object); statusOf no depth limit; jsonModeHint z.toJSONSchema can throw after charge; getChat caches rejected import; time budget only checked at next charge (up to one request overrun); createLangChainCall result handling untested
Task 9: complete (commits d5c651b..03c12ce, review clean)
Task 10: dispatched (base 03c12ce, implementer sonnet)
Task 10: implemented 6cf718e (DONE_WITH_CONCERNS: llm.ts wraps non-object schemas as {items} for json_schema (proxy rejects array root); LLM errors propagate from researchOne; stale returned when fresh search empty; weak sourceUrls (Facebook/Yelp) pass). Live: 8 facts groceries Austin, 14 facts w/ prices fast food. Under review.
Task 10: note → Task 12 research node: catch non-budget LLM errors per category → drop category with warning.
Task 10: minor (deferred): stale fallback on empty fresh results never refreshes (re-spends 2 queries each run); createTavilyClient/parseTavilyResponse untested; redactKey slices before redacting; widenToState "Texas, Texas" cosmetic; narrow URL normalization (http/https, www); cosmetic ids "-austin-austin"
Task 10: complete (commits 03c12ce..6cf718e, review clean)
Task 11: Ruling: add research scope 'national' — RecurringItem.factId is required but subscriptions/SaaS/insurers/IRS/payment processors/payroll providers are national, and Task 10 drops facts whose state ≠ loc.state. National facts carry state 'US', skip the state filter and state retry, cache key `US:<category>`; R7 skips facts with state 'US' — cost if wrong: schema enum + 3 small branches.
Task 11: dispatched (base 6cf718e, implementer opus)
Task 11: implemented 246bdf6 (DONE_WITH_CONCERNS: llm.ts now sends plain non-strict JSON Schema (proxy strict mode rejects oneOf); live end-to-end: final run R1–R10 pass, 9 LLM calls; lifeModel 45–85s/call; one fact per item; scheduler can post credit-side payment on Saturday). Under review.
Task 11: note → Task 12: card.ts credit-side payment postedDate must be a bank business day (pick k∈{0,1,2} only where checking+k is a business day; k=0 always valid) — keeps R5 [0,2].
Task 11: note → Task 12/17: lifeModel latency 45–85s vs 120s timeout and 10-min job budget — watch in live smoke.
Task 11: Ruling: feasibility check uses worst-case per-cycle counts (weekly 5, biweekly 3, irregular perMonth.max, monthly 1, taxDates 1) compared against max (not averages vs max−1) — the scheduler throws on the actual draw, so averages let seeds fail — cost if wrong: lifeModel slightly more conservative.
Task 11: minor (deferred, realism-relevant — triage in final review): one model item per fact (templates keyed per merchantKey not per account) — grocery chain can't appear on both debit & credit; prompts/descriptors.ts:842 contradicts
Task 11: minor (deferred): fact text not marked untrusted + no length cap in compactFact; planResearch mentions() matches IN/OR/ME as words; 6-query minimum checked pre-dedupe; prompt size unbounded (facts/audit rows); randomPersona strips foreign partial keys / zip format-only / plain Error on unknown state; RESERVED_KEYS/BUILTIN_KEYS duplicated; payer-is-merchant false positive (Square/PayPal); test gaps listed in review
Task 11: fix round 1/5 dispatched — Important: feasibility averages vs scheduler worst case; no credit-limit sanity check
Task 11: fix round 1/5 (2 addressed, 0 open; commits 246bdf6..156eb74)
Task 11: minor (deferred): worst-case count misses monthly item rolling twice into one cycle / two card payments per cycle; no headroom for runtime overdraft fees at total==max; spend estimate ignores tips
Task 11: complete (commits 6cf718e..156eb74, review clean)
Task 12: dispatched (base 156eb74, implementer opus)
Task 12: implemented 34f3051 (DONE_WITH_CONCERNS: live run completed, 0 rounds, R1–R10 pass, 22 LLM / 15 Tavily / 208s (lifeModel 83s, research 71s); test 2 uses R7 not R6; root @bh/core loads langgraph eagerly). Under review.
Task 12: realism observations from live result (triage at final review/Task 17): `PAYPAL TRANSFER PAYPAL` income descriptor; ACH bill descriptors end with random REF (e.g. `SILVER CREEK APA RENT W4MYRQRSO5`); same-day repeat H-E-B visits; Adobe at pretax price
Task 12: minor (deferred): resume requires profile before resume branch (doc says request ignored); research node_end missing on mid-research resume + cast; uniqueFacts silently collapses same-id facts across categories; test gaps (mixed rounds, AggregateError, resume mid-research, brittle R8 seed); R6 graph path untested; time budget checked at charge only; fixtureResult.ts 2237 generated lines
Task 12: complete (commits 156eb74..34f3051, review clean)
Task 13: dispatched (base 34f3051, implementer sonnet)
Task 13: implemented 1f78a77 (DONE_WITH_CONCERNS: test file 311 lines; queued→interrupted on startup; implementer killed two foreign processes `node --watch src/index.ts` (PIDs 75826/75828, parent 75677) during smoke cleanup — surfaced to user). Under review.
Task 13: minor (deferred): coded-error filter too broad (ENOENT/SQLITE_* messages could land in error_json) — allowlist core codes; pre-check catches all errors as INFEASIBLE (should instanceof); stuck job if setStatus/finishGeneration throws (DB failure); missing tests (disconnect, XFF, FIFO/≤2 with 4 jobs, 500 path); budget-failed result shape {partial, statements} — Tasks 14–16 must handle; test file 311 lines + `void FIXTURE_TODAY` hack
Task 13: note → Task 14/16: in LLM_FAKE mode persona-less jobs only succeed when the random metro is Austin — fixture persona/research must work for any metro (or fixture persona pins Austin).
Task 13: fix round 1/5 dispatched — Important: cross-site simple POST can drive loopback bypass (no Content-Type check)
Task 13: fix round 1/5 (1 addressed, 0 open; commits 1f78a77..13ee3b0)
Task 13: minor (deferred): Origin check blocks a keyed remote web UI on LAN IP
Task 13: complete (commits 34f3051..13ee3b0, review clean)
Task 14: Ruling: add Host allowlist (localhost/127.0.0.1/[::1], any port) for loopback-bypassed requests — Task 14 adds a list route, so DNS-rebinding reads become meaningful — cost if wrong: one middleware.
Task 14: dispatched (base 13ee3b0, implementer sonnet)
Task 14: implemented def7b27 (DONE_WITH_CONCERNS: list returns {items}; DELETE 409 for queued too; persona route cap 4 calls; GraphDeps.personaDefaults added in core for fake Austin pin; business persona-less jobs fail in fake mode (fixture personal-only); Host check via c.req.url). Under review.
Task 14: Ruling: list response is `{items:[...]}` and DELETE returns 409 for queued as well as running — wrapper leaves room for paging; deleting a queued row would let the runner start a job against a missing row — cost if wrong: tiny client/route tweak.
Task 14: minor (deferred): host guard reads c.req.url not Host header (absolute-form edge); business jobs/personas fail in LLM_FAKE (persona route returns 502); feasibility try/catch duplicated + inline JSON parse in POST generations; partial:null → {}; misc test file 399 lines; persona-failure tests only LlmCallError; await between status check and delete
Task 14: complete (commits 13ee3b0..def7b27, review clean)
Task 15: dispatched (base def7b27, implementer sonnet)
Task 15: implemented 2b0bf2e (DONE_WITH_CONCERNS: address single 🎲; client rule endMonth ≤ last month; fake mode: business persona 500, checking-only LIFE_MODEL_INVALID (fixture uses credit); ports 5173/8787 held by user's company-helper dev (`/Users/tian/.codex/worktrees/main-integrated/company-helper`), implementer used 8799/5199 + BH_API_TARGET). Under review.
Task 15: Ruling: move this project's default ports off the user's company-helper ports — server PORT default 8797, web dev 5197 (proxy target from env) — they collide with a dev stack the user runs daily — cost if wrong: two config values. Carried into Task 16.
Task 15: Ruling: accept one 🎲 for the whole address (server partial schema treats address atomically) and the client rule "结束月份不能晚于上个月" (an unfinished month can't have a full statement) — cost if wrong: small UI tweak.
Task 15: minor (deferred): concurrent roll races (snapshot partial, overwrite typed field, 'all' vs per-field); wrong-shape persona silent; server error heading always same + stale; step 3 shows account error on first entry; slider thumbs stuck at equal values; pointless useCallback; local ProgressEvent mirror may drift; placeholder contrast
Task 15: complete (commits def7b27..2b0bf2e, review clean)
Controller: .env PORT changed 8787 → 8797 (port ruling).
Task 16: dispatched (base 2b0bf2e, implementer sonnet)
Task 16: implemented 8c49d83 (DONE: ports 8797/5197; live node highlighting unit-tested only; failed/interrupted/partial/409 unit-tested only; 4s status poll added). Under review.
Task 16: Ruling: header period shown MM/DD/YYYY (period spans months/years; rows/cards stay MM/DD) and credit month card label "还款与退款" instead of "收入" (credit cards have no income) — cost if wrong: two strings.
Task 16: minor (deferred): reconnect banner not cleared on onopen; refetch failure after done replaces progress with error card; poll untested; raw txn type / suggestedTarget shown in English; history capped at 200 w/o hint; tab key collision; no StrictMode test
Task 16: complete (commits 2b0bf2e..8c49d83, review clean)
Task 17: dispatched (base 8c49d83, implementer sonnet)
Task 17: implemented e94cb13 (DONE_WITH_CONCERNS: personal Chicago trucker completed 24 LLM/15 Tavily/326s, R1–R10 pass, 1 descriptors retry; business Austin cafe FAILED LLM_OUTPUT_INVALID at 385s — lifeModel retry after audit emitted unitPriceCents:0 for non-fuel items, repair failed, whole result lost, usage not recorded). Under review.
Task 17: LOAD-BEARING → final fix wave: (1) lifeModel schema failure on a retry round must fall back to the last valid model/result with warning instead of failing the job; normalize unitPriceCents 0/non-fuel → absent before parse; record usage on failure. (2) ACH REF suffix on bill descriptors; same-day repeat visits; descriptor vs evidence contradictions (payer name vs evidence, store # mismatch, ACH type on credit card, store # on single-location shop).
Task 17: Ruling: business job failure and missing business realism check are deferred to the final fix wave + post-fix live re-run (pipeline defect, not script) — cost if wrong: none.
Task 17: minor (deferred): fetchEvents swallows parse errors; no per-request timeout in smoke api(); report header period vs credit cycle wording
Task 17: fix round 1/5 dispatched — Important: smoke R1–R10 gate passes on empty/missing rules
Task 17: fix round 1/5 (1 addressed, 0 open; commits e94cb13..ce54b76)
Task 17: minor (deferred): entry guard silent on symlinked path; duplicate ruleId masked
Task 17: complete (commits 8c49d83..ce54b76, review clean)
Final review: dispatched (opus) over 50f7f1d..ce54b76
Final review: findings C1, I1–I11, must-fix minors (R7 sample guard, error-code allowlist), M1/M5/M6 → $D/final-findings.md
Final review: Ruling: I10 (same-seed reproducibility across LLM nodes) deferred — relabel the button instead of building a replay/LLM cache this wave; spec criterion 4 holds for the code layer only until then — cost if wrong: user expects identical re-runs.
Final review: Ruling: merge gated on a passing live smoke for BOTH personal and business jobs after the fix wave (agree with reviewer).
Final fix wave: dispatched (base ce54b76, implementer opus)
Final fix wave: done 9be6234..06219f8 (all findings fixed; live: personal completed R1–R11 24/14 276s; business completed_with_warnings R1–R11 29/14 556s, 4 high audit findings on model content). Scoped re-review dispatched.
Final fix wave: re-review clean (all addressed; minors: resumed-queued job re-enqueued with resume:false on restart; "rental" housing regex; evidence notes outside untrusted block in audit; cheaperTxn ignores perMonth.max; zod reason in LLM_OUTPUT_INVALID; housing fallback budget race)
