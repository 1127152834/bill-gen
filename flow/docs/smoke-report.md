# Live smoke report

Date: 2026-10-29 (statement period: September 2026, the default last full month)

## Environment

- Server: `pnpm --filter @bh/server start`, `127.0.0.1:8797`, real `.env` (keys not recorded here).
- LLM: OpenAI-compatible proxy, model `gpt-6.1-sol`, `LLM_STRUCTURED_MODE=json_schema`.
- Web search: real Tavily.
- Driver: `LIVE_SMOKE=1 pnpm smoke` (`scripts/smoke.ts`), run once. Results are saved under `data/smoke/` (gitignored).
- Budgets per job: 40 LLM calls / 25 Tavily queries / 10 min.

## Jobs

| | Personal (random persona, checking 8-13 + credit 15-25, 1 month) | Business (Pecan Street Coffee Co LLC, Austin TX, 12 staff, checking 20-30, 1 month) |
|---|---|---|
| Status | `completed` | `failed` (`LLM_OUTPUT_INVALID`) |
| Wall time | 326 s | 385 s |
| LLM calls | 24 | not recorded (failed jobs store no usage); 2 `lifeModel` passes + at least the planning calls |
| Tavily queries | 15 (14 research events + planning) | 15 research queries before the failure |
| Pipeline rounds | 1 retry (`descriptors`, "audit: 2 high finding(s)") | 1 retry (`lifeModel`, "audit: 2 high finding(s)"), then crashed inside the retried `lifeModel` |
| Rules R1-R10 | all 10 pass (11 checking rows, 25 credit rows) | R1-R10 all passed on the first validate, but the job never produced a result |
| Warnings | 2: `category rent dropped: no sources`, `category state taxes and trucking fees dropped: no sources` | none stored |
| Audit findings left | 2 (1 medium: no rent/mortgage in checking; 1 low: single $5,950 fuel-card debit) | n/a |

Personal persona: Daniel Navarro, 42, owner-operator trucker, Chicago IL 60632, married, 2 children.

### Business failure

First `lifeModel` pass, schedule, descriptors and validate (R1-R10 all green) succeeded. The audit then raised 2 high findings, which sent the graph back to `lifeModel`. The retried `lifeModel` output was invalid even after the one repair attempt:

```
discretionaryPool.1..N.unitPriceCents: Too small: expected number to be >0
```

The model emitted `unitPriceCents: 0` for non-fuel discretionary items instead of omitting the field (`unitPriceCents` is `positiveCents.optional()` in `packages/core/src/schemas/model.ts`). The whole job failed with no partial result, although a valid first-pass life model and a rule-clean schedule existed in the checkpoint. This is a pipeline/prompt problem, not a smoke-script problem, so the business job was not re-run (the brief allows a re-run only for script faults). **No business statement exists, so the business realism check below could not be done.** To retry: start the server and run `LIVE_SMOKE=1 pnpm smoke` again (it submits both jobs), or POST the business body from `scripts/smoke.ts` by hand.

## Realism check: personal checking (Harborline Bank ...3652, 2026-09-01 to 2026-09-30)

Opening $12,864.73, closing $15,789.45. Full transaction list:

| # | Date | Description | Amount |
|---|---|---|---|
| 1 | 2026-09-04 | ACH CREDIT MAPLE RIDGE LOADS | +5,020.89 |
| 2 | 2026-09-08 | CAG TRUCK CAPITA PAYMENT D1X413YTNE | -2,450.00 |
| 3 | 2026-09-11 | ACH CREDIT MAPLE RIDGE LOADS | +4,101.65 |
| 4 | 2026-09-14 | PROGRESSIVE INS PREM ZUOMSWTWUT | -1,384.75 |
| 5 | 2026-09-15 | IRS USATAXPYMT 0GEAM28X74 | -2,400.00 |
| 6 | 2026-09-18 | ACH CREDIT MAPLE RIDGE LOADS | +3,919.78 |
| 7 | 2026-09-18 | COMED WEB PMTS S8PZUAHH5L | -139.34 |
| 8 | 2026-09-22 | RTS PAYMENT O9GVVUSA3U | -5,950.00 |
| 9 | 2026-09-23 | ONLINE PMT TO CARD ENDING 4992 | -1,796.32 |
| 10 | 2026-09-24 | PEOPLES GAS WEB PMTS 7QAJ9O8165 | -58.99 |
| 11 | 2026-09-25 | ACH CREDIT MAPLE RIDGE LOADS | +4,061.80 |

Judged: (a) merchant exists in that city / is verifiable, (b) descriptor format believable, (c) amount plausible. Evidence pages were opened with WebFetch/WebSearch where they were reachable.

| # | Merchant | (a) exists | (b) descriptor | (c) amount | Note |
|---|---|---|---|---|---|
| 1 | "Maple Ridge Loads" (evidence merchant: C.H. Robinson, Eden Prairie MN) | ✗ | ⚠ | ✓ | The descriptor payer does not match the merchant/evidence (C.H. Robinson); evidence is a freight-factoring blog (reilcap.com), not a payer page. The payer name looks invented. |
| 2 | CAG Truck Capital, Chadds Ford PA | ✓ | ⚠ | ✓ | Real truck lender (4 Hillman Dr, Chadds Ford PA, found by search). Truncated name `CAPITA` is realistic; the trailing random 10-char ref (`D1X413YTNE`) is not. $2,450 is plausible for a rig loan. |
| 3 | same as #1 | ✗ | ⚠ | ✓ | Same payer mismatch. |
| 4 | Progressive Commercial, Mayfield Village OH | ✓ | ⚠ | ✓ | Real insurer. `PROGRESSIVE INS PREM ZUOMSWTWUT`: random letters as ref are not believable; $1,385/mo plausible for owner-operator truck insurance. |
| 5 | IRS | ✓ | ⚠ | ✓ | `IRS USATAXPYMT` is the real descriptor stem (confirmed by search); real ones carry a numeric trace, not `0GEAM28X74`. $2,400 estimated tax: plausible for ~$17k monthly deposits. |
| 6 | same as #1 | ✗ | ⚠ | ✓ | |
| 7 | ComEd, Chicago | ✓ | ⚠ | ✓ | Real utility for Chicago. Random ref after `WEB PMTS`. $139 plausible. |
| 8 | RTS Financial, Overland Park KS | ✓ | ⚠ | ⚠ | Real fuel-card/factoring company (9345 Metcalf Ave, found by search). A single $5,950 payment is large for a weekly-limited fuel card (audit also flagged it, low). |
| 9 | own credit card | ✓ | ✓ | ✓ | $1,796.32 equals the card statement balance; consistent with the credit statement. |
| 10 | Peoples Gas, Chicago | ✓ | ⚠ | ✓ | Real utility; random ref; $59 plausible. |

Overall: the structure is credible (income weekly, tax, insurance, loan, utilities, card payment) but every ACH bill descriptor carries a random 10-char ref, and the income payer is inconsistent with its evidence. No rent/mortgage for a married homeowner/renter with 2 children (audit medium finding left as a warning). Only 11 rows fit the requested 8-13, so nothing in this account is a retail purchase.

## Realism check: personal credit (Harborline Bank ...4992, cycle 2026-08-02 to 2026-09-01)

25 rows, limit $10,000, APR 23.99%, statement balance $1,796.32, minimum due $35. Ten rows checked:

| # | Date | Description | Amount | (a) exists | (b) descriptor | (c) amount | Note |
|---|---|---|---|---|---|---|---|
| 1 | 08-02 | PETES PRODUCE CHICAGO IL | 121.04 | ✓ | ✓ | ⚠ | Real (1411 W 87th St and 1543 E 87th St, Chicago). Both stores are on the far South Side, ~10 miles from the persona's 60632 home; high for a produce store but possible. |
| 2 | 08-09 | PETES PRODUCE CHICAGO IL (x3 same day) | 79.22 / 173.40 / 107.74 | ✓ | ✓ | ✗ | Three charges at one store on one day totalling $360. Not believable. |
| 3 | 08-04 | TAQUERIAS ATOTONILCO #9617 CHICAGO IL | 24.53 | ✓ | ⚠ | ✓ | Real (3916 W 26th St, Little Village, price "$"). A single independent taqueria would not show a `#9617` store number. |
| 4 | 08-05 | AMZN MKTP US*33TMSEZ97D AMZN.COM/BILL WA | 29.78 | ✓ | ✓ | ✓ | Correct Amazon marketplace format. 5 Amazon charges in 3 weeks is plausible for a family of four. |
| 5 | 08-06 | ELDMANDATE.BIZ CA | 25.00 | ✓ | ✓ | ✓ | Vendor sells ELD devices; the page states $25/month. Good fit for a trucker. Posted 08-10 (4 days) is slow for a subscription. |
| 6 | 08-10 | COMCAST 800-934-6489 PA | 45.00 | ✓ | ⚠ | ✓ | Xfinity exists, descriptor style fine. But it is typed `ACH` on a credit-card statement, and the evidence price is $30 (NOW Internet 100) vs $45 charged. |
| 7 | 08-08 | JEWEL-OSCO #5989 CHICAGO IL | 89.24 | ✓ | ✓ | ✓ | Jewel-Osco at 2520 N Narragansett Ave exists (the evidence is its Meat & Seafood department page). Store number format is real. Persona lives ~7 miles away. |
| 8 | 08-22 | WAL-MART #0860 CHICAGO IL | 27.30 | ✓ | ✗ | ✓ | The evidence points to Walmart Supercenter #5402 (4650 W North Ave); the descriptor says `#0860`, a different store number. |
| 9 | 08-22 | LA CATEDRAL CAFE CHICAGO IL | 90.99 | ✓ | ✓ | ⚠ | Real (2500 S Christiana Ave, price "$", $5-15 per meal). $91 and a $55 charge a week later are high for it. |
| 10 | 08-19 | USMOBILE.COM NY | 100.00 | ✓ | ✓ | ✓ | Real carrier; the evidence says four lines at $100/month. Typed `ACH` on a credit card statement. |

## Previously observed issues: still present?

- `PAYPAL TRANSFER PAYPAL` income descriptor: not observed in this run (no PayPal income for the trucker persona). Not verified fixed.
- ACH bill descriptors ending in a random 10-char ref (e.g. `SILVER CREEK APA RENT W4MYRQRSO5`): **still present**, on 6 of 7 ACH debits (`CAG TRUCK CAPITA PAYMENT D1X413YTNE`, `PROGRESSIVE INS PREM ZUOMSWTWUT`, `IRS USATAXPYMT 0GEAM28X74`, `COMED WEB PMTS S8PZUAHH5L`, `RTS PAYMENT O9GVVUSA3U`, `PEOPLES GAS WEB PMTS 7QAJ9O8165`).
- Repeat same-day visits to the same grocery store: **still present** (Pete's Produce x3 on 2026-08-09; also Pete's Produce on 08-29 and 08-30, Taquerias Atotonilco on 08-29/30).
- Adobe charged at pretax price: not observed (no Adobe in this persona). Not verified fixed.

## Findings -> follow-ups (not fixed in this task)

1. **Business job crashes when the retried `lifeModel` output is invalid.** The model writes `unitPriceCents: 0` for non-fuel pool items. Fixes: normalise `0`/`null` to "absent" for non-fuel items before schema validation (or make the schema `preprocess` it), tell the prompt explicitly to omit the field, and on a retry-node failure fall back to the last valid `lifeModel` (with a warning) instead of failing the whole job.
2. **ACH descriptors with random 10-char refs** look synthetic. Use bank-style refs by biller family (numeric trace for IRS, `WEB PMTS` with no ref for utilities, an account-like number for lenders) or omit the ref.
3. **Same-day repeats at one merchant** (3 charges at one produce store in a day). Add a scheduler cap (for example max 1 discretionary visit per merchant per day, with a minimum gap for grocery/produce) and a per-visit ticket range per merchant `priceTier`.
4. **Income payer vs evidence mismatch.** `ACH CREDIT MAPLE RIDGE LOADS` is backed by C.H. Robinson/factoring-blog evidence. The descriptors step should derive the payer from the merchant name, or the life model should only invent payer names that the evidence page supports.
5. **Credit-card rows typed `ACH`** (Xfinity, US Mobile). Card recurring charges should be `POS`; restrict `ACH` to checking.
6. **Store numbers on independent restaurants** (`TAQUERIAS ATOTONILCO #9617`) and a store number not matching the evidence store (`WAL-MART #0860` vs #5402). Add store numbers only for chains, and take the number from the evidence when available.
7. **Evidence quality.** Several evidence URLs are weak proxies (a factoring blog for a freight payer, a department sub-page for a supermarket, an Amazon Prime price for an Amazon order). Prefer a page for the merchant itself and pick the pricing note that matches the transaction.
8. **Amount plausibility on cheap venues** ($91 at a "$5-15" cafe; $173 at a produce store). Cap tickets by the merchant's price tier from evidence.
9. **Missing rent/mortgage** for a household persona (audit medium finding left as a warning, plus `category rent dropped: no sources`). Research should retry rent with a different query, or the life model should infer rent from the persona's housing type.
10. **Smoke script / ops.** Failed jobs store no `usage`, so cost cannot be read after a failure; persist usage (or the last checkpoint) in `error_json` or the partial result.

---

# After fix wave (2026-10-29)

Fix wave commits `9be6234..e6da5b2` (pipeline fallbacks, bank-style references, scheduler realism, credit limit + R11, housing research, untrusted web text). Same environment as above, one live run: `DATABASE_PATH=./data/fixwave.db pnpm --filter @bh/server start` on `127.0.0.1:8797` (a fresh database, so the research cache of the first run was not reused), then `LIVE_SMOKE=1 pnpm smoke`. The smoke gate now requires R1-R11.

## Jobs

| | Personal (random persona, checking 8-13 + credit 15-25, 1 month) | Business (Pecan Street Coffee Co LLC, Austin TX, 12 staff, checking 20-30, 1 month) |
|---|---|---|
| Status | `completed` | `completed_with_warnings` |
| Pipeline rounds | 1 send-back (`lifeModel`, "audit: 1 high finding(s)") | 2 send-backs (`lifeModel` twice, "audit: 2 / 3 high finding(s)"), then out of rounds |
| LLM calls / Tavily | 24 / 14 | 29 / 14 |
| Wall time | 276 s | 556 s |
| Rules R1-R11 | all 11 pass (10 checking rows, 17 credit rows) | all 11 pass (20 business checking rows) |
| Warnings | 1: `category state estimated taxes dropped: no sources` | 2 research drops (`food and packaging`, `sales tax`) + 4 high audit findings left after the last round |
| Audit findings left | 1 low (the dropped category, now written into the audit report) | 4 high, 4 medium, 3 low (2 of the low ones are the dropped categories) |

Personal persona: Marcus Ellison, personal trainer, Virginia Beach VA 23456. Income: weekly Stripe payouts. Rent (Pipers Landing Apartments, $1,650) is present this time.

The business job ending `completed_with_warnings` is the spec 4.4 behaviour (rounds used up -> output with warnings). The four high findings it left are model-level realism gaps, not rule failures: Gusto payroll as one debit instead of NET/TAX/FEE, no sales-tax remittance (that research category was dropped), a water/wastewater bill whose evidence is a returned-payment fee, and no dairy/packaging purchases (also a dropped category).

## Realism check: business checking (Harborline Bank ...5981, 2026-09-01 to 2026-09-30)

Opening $38,746.53, closing $76,296.83. Ten of the 20 rows. Merchants were checked with web searches where the evidence was not conclusive.

| # | Date | Description | Amount | (a) exists | (b) descriptor | (c) amount | Note |
|---|---|---|---|---|---|---|---|
| 1 | 09-01 | SQUARE DEPOSIT | +16,040.90 | ✓ | ⚠ | ⚠ | Square is the processor in the evidence. Real Square settlements read `SQUARE INC ...` and arrive daily; here all five batches land on Tuesdays (audit medium). ~$64k/month for a 12-staff cafe is plausible. |
| 2 | 09-01 | ATLAS N LAMAR UN RENT | -5,250.00 | ⚠ | ✓ | ✓ | The evidence is a PropertyShark retail listing on N Lamar, not a landlord, and the cafe's address is E 6th St. $5,250 = about 2,100 sq ft at the quoted $30/sq ft/yr. |
| 3 | 09-01 | POS PURCHASE UPPER CRUST BAKERY | -1,614.95 | ✓ | ⚠ | ⚠ | Real bakery at 4508 Burnet Rd (BBB, Austin Chronicle). No city/state on a debit-card line (audit low). Four orders of $1.3-1.9k a month is a lot of pastry for one cafe. |
| 4 | 09-04 | GUSTO PAYROLL 823111441 | -10,200.00 | ✓ | ⚠ | ✓ | Gusto is real; the fixed company id is now numeric. Real Gusto splits NET / TAX / FEE (audit high). Evidence quotes an EOR fee, not payroll. $10.2k biweekly for 12 mostly part-time staff is plausible. |
| 5 | 09-08 | THE HARTFORD INS PREM | -220.00 | ✓ | ✓ | ✓ | Evidence page is The Hartford's coffee-shop BOP page; $220/mo matches its quoted average. |
| 6 | 09-10 | INSUREON INS PREM | -88.00 | ✓ | ⚠ | ✓ | Insureon is a broker (Chicago), so a premium normally goes to the carrier; $88/mo workers' comp matches the quoted average. |
| 7 | 09-16 | AUSTIN ENERGY WEB PMTS | -1,421.94 | ✓ | ✓ | ⚠ | Real utility, clean bill-pay line (no random ref). High but possible for a commercial espresso bar; the evidence only quotes the $16.50 base charge. |
| 8 | 09-18 | CITY OF AUSTIN U WEB PMTS | -344.85 | ✓ | ✓ | ⚠ | Austin water/wastewater bills as City of Austin Utilities. Amount plausible, but the quoted evidence is a $30 returned-payment charge (audit high). |
| 9 | 09-22 | POS PURCHASE ARMADILLO COFFEE ROASTER | -1,124.36 | ✓ | ✓ | ✓ | Real Austin wholesale roaster (directory listings, Evil MoPac profile). Name cut like a real 22-char merchant field. One bean order a month is light (audit medium). |
| 10 | 09-23 | ALTA PEST CONTRO PAYMENT | -59.00 | ✓ | ✓ | ✓ | Real Austin pest-control company (Wells Branch Pkwy); $59/mo matches the evidence. |

Overall: every merchant exists, every ACH line reads like real bill pay (no random letters), the fixed id on Gusto is numeric, payroll exists only because this is a business. Remaining issues are about the model (payroll split, sales tax, supply mix, weekly Square batches) and evidence quality (a listing as landlord, wrong-price evidence for Gusto and the water bill).

## Earlier realism defects: re-check

| Defect (first run) | Status | Evidence in this run |
|---|---|---|
| ACH bills ending in a random 10-char ref (`COMED WEB PMTS S8PZUAHH5L`) | **fixed** | `DOMINION ENERGY WEB PMTS`, `COX COMMUNICATIO WEB PMTS`, `INSURANCE CANOPY INS PREM`, `AUSTIN ENERGY WEB PMTS`, `THE HARTFORD INS PREM`; IRS now `IRS USATAXPYMT 270611597617618` (15-digit numeric trace); Gusto `823111441` is a fixed numeric id. |
| Same-day repeats at one merchant (Pete's Produce x3) | **fixed** | No merchant has two purchases on one day in either job; grocery visits are a week apart (Trader Joe's 08-22 / 08-29 / 09-05, Aldi 08-23 / 09-12). |
| Income payer contradicting its evidence (`MAPLE RIDGE LOADS` vs C.H. Robinson) | **fixed** | Income lines are `STRIPE TRANSFER ST-860296006` with Stripe evidence and `SQUARE DEPOSIT` with Square evidence. (The Stripe evidence page is a blog post about payment tools: weak but consistent.) |
| Store number not matching the evidence (`WAL-MART #0860` vs #5402) / on independents (`TAQUERIAS ATOTONILCO #9617`) | **fixed** (with a note) | Independents carry none (`AZARS MEDITERRANEAN`, `THREE SHIPS COFFEE`, `CLEAN EATZ`). Chains with no stated store number still get a fixed synthetic one (`TRADER JOES #1623`, `SAMS CLUB GAS #0689`); none of this run's evidence pages stated one, so nothing contradicts. Only DICK'S Sporting Goods #145 was extracted with a stated number (not used in the model). |
| `ACH` rows on the credit card (Xfinity, US Mobile) | **fixed** | All 17 credit rows are `POS` (Disney+, Acuity Scheduling, AT&T included), plus the `PAYMENT`. |
| `PAYPAL TRANSFER PAYPAL` | **fixed in code, not seen live** | No PayPal income in this run; the payer-first templates never repeat a prefix word (unit-tested: `paypal` -> `PAYPAL TRANSFER`). |
| No rent/mortgage for a household (`category rent dropped`) | **fixed for this run** | Rent present (Pipers Landing Apartments $1,650). The housing fallback queries were not needed this time. |

## Follow-ups (not in this wave)

1. **Same-seed reproduction (spec criterion 4).** Not built in this wave (controller ruling): the web button now reads "用相同参数和 seed 重新生成" and says research and model output can differ. Exact replay needs a model/research answer cache keyed by request + seed.
2. **Evidence quality.** Facts can cite a page that only mentions the merchant (Trader Joe's backed by a Kroger ad page, AT&T by a Cox page, a property listing as landlord). Extraction could require the page to be about the merchant itself.
3. **Business payroll and tax realism.** Gusto as NET/TAX/FEE lines, sales-tax remittance, daily card settlements; the audit flags them, but two rounds were not enough for the model to fix them.
4. Deferred from the review: resume resets the budget (M2), fuel gallons range (M3), one model item per fact, history pagination.
