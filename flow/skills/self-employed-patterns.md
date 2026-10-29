# Self-employed / 1099 personal personas: income and spending patterns

Personal personas in this game are ALWAYS self-employed or gig workers. They have no employer,
no payroll deposit, no pay stub, no 401(k) contribution line. Their money arrives from
platforms, clients and customers in uneven pieces. Use this file to choose income streams,
cadence, amounts, seasonality and the expenses that go with each trade.

## 1. Rules that apply to every 1099 persona

- Never create an income stream that reads as salary: no `PAYROLL`, `DIRECT DEP`, `SALARY`,
  no identical biweekly amount from one employer. A real gig deposit is net of fees and
  varies by 10-40% week to week.
- Income channels (model enum) and what they look like on a statement:
  - `platform_payout`: Uber, DoorDash, Instacart, Etsy, Airbnb, Upwork. Weekly or per-event.
  - `ach_client`: a business pays an invoice by ACH. Round-ish amounts, irregular days.
  - `stripe` / `square` / `paypal` / `venmo`: processor payouts, net of 2.6%-3.5% fees.
  - `zelle` / `mobile_deposit`: small clients and customers; round amounts (cash-like).
  - `card_sales`: only for businesses with a storefront (see industry-cost-structures).
- Payout timing (use these when choosing `cadence`):
  - Rideshare: weekly deposit, mid-week (Wednesday is typical); instant cash-out any day for a
    fee of roughly $0.50-$1.85 per cash-out, usually netted as a separate small deposit.
  - DoorDash: weekly, earnings Monday-Sunday paid the following Monday-ish; Fast Pay $1.99.
  - Instacart: weekly; Instant Cashout $0.50 each.
  - Etsy: weekly by default, on Mondays; seller can pick daily/weekly/biweekly/monthly.
  - Square: next business day after sale (before ~5 PM PT cutoff), same-day with ~1.95% fee.
  - Stripe: rolling, about 2 business days after the charge (first payout of a new account
    7-14 days).
  - Upwork: hourly contracts paid about 10 days after billing; ACH withdrawal to an American checking account
    is free and takes 3-5 business days; fixed-price released after client approval.
  - Airbnb: payout released about 24 hours after guest check-in, then 1-5 business days to the
    bank. Venmo standard cash-out free, 1-3 business days.
- Amount shape by channel: processor payouts have odd cents (net of fees, e.g. 641.37);
  client ACH is round-ish (1,250.00; 2,400.00; 3,180.00); Zelle/Venmo/check from customers are
  round (60, 85, 120, 250). Never make every deposit end in .00.
- Months are lumpy: one slow month at 40-60% of average is realistic, never all 12 identical.
  Model it with the 12 `seasonality` multipliers (index 0 = January); average near 1.0.
- Model limits: recurring items are `dayOfMonth` 1-28, `taxDates` or `biweekly`; income cadence is
  weekly, biweekly, monthly or `irregular {perMonth}` (use `irregular` for daily card payouts and for
  per-job deposits). Weekly fuel, supplies etc. are discretionary-pool entries, not recurring items.
- Idle weeks happen: weekly streams may skip 0-2 weeks a year (illness, vacation, slow season).

## 2. Taxes and self-employment (very visible on a statement)

- Federal estimated tax due dates: April 15, June 15, September 15, January 15 of the next
  year (shift to the next business day if weekend/holiday, e.g. June 16, 2025). Use
  `tax_estimated` with `dayRule: taxDates`, priority 1, paid from checking, descriptor
  `IRS USATAXPYMT`. Payment is a rounded dollar amount (e.g. 850.00, 1,200.00, 2,500.00).
- Rule of thumb for amount: 15%-30% of that quarter's net profit (self-employment tax is
  15.3% on 92.35% of net earnings, plus income tax). Low earners (< ~$30k/yr net) pay $0-$400
  per quarter or skip some quarters; $60k net pays about $1,500-$3,000 per quarter.
- Safe-harbor logic: people often pay equal installments (100% of last year's tax; 110% if
  last year's income was over $150,000), so four near-identical payments are realistic.
- State estimated taxes only if the state has an income tax. NO state income tax: AK, FL, NV,
  NH, SD, TN, TX, WA, WY. A Texas or Florida resident paying `STATE ... TAX PYMT` is a red flag.
- A refund (`IRS TREAS 310 TAX REF`, credit) appears February-April only, if at all, usually
  $300-$3,000.
- Health insurance is often a marketplace plan: monthly ACH debit $250-$700, same day each month.

## 3. Recurring items and priority for 1099 personas

| Item | Kind | Priority | Notes |
|---|---|---|---|
| Rent / mortgage | rent / loan | 1 | Same day each month, 1st-5th; never skipped |
| Car loan / lease | loan | 1-2 | Fixed amount, ACH, fixed day |
| Estimated taxes | tax_estimated | 1 | `taxDates` rule |
| Auto / renters insurance | insurance | 2 | Monthly or semi-annual; monthly is fixed |
| Phone, internet | utility | 2-3 | Slight monthly variation on phone bill |
| Electric, gas, water | utility | 3 | Seasonal amount (see us-calendar-seasonality) |
| Streaming, software, gym | subscription | 4-5 | Skippable in a tight month |

- Priority 1-2 are never skipped. A slow month cuts discretionary spending first.
- Opening balance: about 2-6 weeks of fixed costs for most gig workers ($600-$3,500); a stable
  consultant or high earner $4,000-$15,000. Never exactly round (use e.g. $1,847.52).
- Many gig workers carry a credit card for gas/groceries: limit $1,500-$7,500; partial payments
  are common (minimum or round $100-$300).

## 4. Trades

### Trade: Rideshare driver (Uber / Lyft)
- Income: `platform_payout` weekly (Wednesday), $350-$1,100 per week for 25-40 hours; plus 0-3
  small instant cash-outs per week ($40-$150) when cash is tight. Seasonality:
  `[0.9,0.9,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.05,1.05,1.15]`.
- Costs: fuel 2-4 fill-ups per week ($35-$60), car payment or rental ($250-$550/mo), commercial
  auto insurance ($180-$350/mo), oil change every ~2 months ($50-$90), car wash ($8-$15),
  tolls, phone. Fast food and coffee on the road: many small rows ($7-$15).
- Descriptor hints: `UBER USA PAYOUT`, `LYFT INC PAYMENT`.

### Trade: Delivery courier (DoorDash / Instacart / Uber Eats)
- Income: `platform_payout` weekly (DoorDash Monday-ish), $250-$900 per week; often two apps on
  different weekdays; Instant Cashout/Fast Pay rows $30-$120 with the fee netted.
- Costs: gas (every 3-5 days), phone plan, hot bag, fast food 6-12 times a month, car upkeep.
- Slow in January-March, busier in rainy/cold months and Nov-Dec.

### Trade: Etsy / handmade goods seller
- Income: `platform_payout` weekly (Monday) or biweekly, $120-$1,800 per deposit. Seasonality:
  `[1.0,0.8,0.8,0.8,0.9,0.8,0.8,0.9,1.0,1.2,1.5,1.5]`. Etsy fees come out of the payout
  (6.5% transaction fee, ~3% + $0.25 processing, $0.20 listings), so deposits have odd cents.
- Costs: supplies (craft stores, Amazon) $80-$600/mo, postage `USPS PO {STORE}` after each batch
  of orders, shipping labels, packaging, Etsy Ads, printer ink, a small studio or storage rent.
- Cash flow: payout can be negative-balance blocked; Q4 supplies are bought in September-October.

### Trade: Freelance designer / writer / developer
- Income: `ach_client` 2-6 invoices/month at $400-$4,500, net-30 terms (paid ~30 days after
  invoice, often the 1st-5th or 25th-30th); Upwork withdrawals ($300-$2,500, 10% platform fee
  already taken); Stripe/PayPal payouts. Seasonality: `[0.8,1.0,1.05,1.05,1.0,0.95,0.85,0.8,1.15,1.15,1.1,0.9]`.
- Costs: software subscriptions $60-$250/mo (Adobe, Figma, GitHub, Notion, domain, hosting),
  coworking $150-$350/mo, coffee shops, internet, health plan.

### Trade: Handyman / small home repair
- Income: `zelle` / `venmo` / `mobile_deposit` per job, $90-$900, 6-16 jobs/month, usually the
  same day as the job; some cash that never touches the account. Seasonality:
  `[0.8,0.8,1.0,1.2,1.2,1.1,1.0,1.0,1.2,1.2,1.0,0.7]`.
- Costs: materials at `HOME DEPOT` / `LOWES` ($30-$400, before the job), fuel, tool purchases,
  general liability insurance ($50-$120/mo), truck payment, phone.

### Trade: House cleaner
- Income: weekly/biweekly regular clients, 6-14 clients at $100-$250 per visit, paid by Zelle,
  Venmo, check (mobile deposit); 3-8 deposits per week on weekdays. Seasonality:
  `[0.95,0.95,1.05,1.15,1.0,0.95,0.9,0.9,1.0,1.0,1.05,1.15]`.
- Costs: cleaning supplies $60-$150/mo (Walmart, Dollar Tree, Amazon), gas, car upkeep,
  insurance, a helper paid by Zelle ($80-$150 per day) if they have one.

### Trade: Landscaper / lawn care
- Income: weekly mowing $35-$75 per lawn (Venmo, Zelle, checks); HOA or commercial contracts
  paid by monthly `ach_client` ($800-$3,500). Seasonality (cold climate):
  `[0.2,0.2,0.6,1.1,1.4,1.4,1.3,1.3,1.3,1.1,0.4,0.2]`; warm climate flatter.
- Costs: equipment fuel and truck fuel (frequent), blades/parts, equipment repair, trailer or
  truck payment, dump fees, seasonal helper cash via Zelle ($100-$250), insurance quarterly.
- Winter: income falls to 10-30% of peak but rent and truck payments continue.

### Trade: Hair stylist / barber (booth renter)
- Income: `square` payouts next business day after the sale (card sales, tips included),
  daily on working days; $90-$450 per working day; some tips in cash. Seasonality:
  `[0.85,0.95,1.0,1.05,1.1,1.0,0.95,1.05,1.0,1.0,1.05,1.2]`.
- Costs: booth rent $500-$1,200/month (weekly $100-$250 rent is modelled as one monthly
  `dayOfMonth` item; Zelle/ACH to the shop owner), supplies at beauty-supply distributors $150-$400/mo, Square fee 2.6% + $0.15, liability
  insurance, licence renewal every two years.

### Trade: Photographer / videographer
- Income: retainers at booking (25-50% of $800-$4,000) plus balance 1-2 weeks before the
  event; Stripe/Square/PayPal/Zelle/ACH; lumpy. Seasonality (weddings): 
  `[0.5,0.5,0.7,0.9,1.3,1.5,1.2,1.2,1.5,1.5,1.0,0.7]`.
- Costs: gear (large one-off $400-$3,500 buys), Adobe, gallery hosting, studio rental,
  second-shooter paid by Zelle ($150-$400), gas, insurance, editing software.

### Trade: Real estate agent (independent contractor)
- Income: commission after closing via the brokerage by ACH: 0-3 closings/month at
  $3,000-$15,000 net of the brokerage split; months with zero income are normal. Seasonality:
  `[0.6,0.6,0.9,1.1,1.3,1.4,1.3,1.2,1.0,1.0,0.8,0.7]` (closings lag listings by 30-60 days).
- Costs: brokerage monthly fee $99-$500, MLS and association dues (quarterly/annual), lead
  gen and ads $200-$1,500/mo, signage and photos, gas, lockbox fees. Big cash buffer: keep
  an opening balance covering 2-3 months and use a credit card for ads.

### Trade: Personal trainer / fitness coach
- Income: 6-20 clients on monthly packages ($120-$600) via Venmo/Zelle/Stripe, mostly on the
  1st-5th; plus drop-in sessions $50-$90. Seasonality: `[1.3,1.1,1.0,1.0,0.95,0.95,0.9,0.9,1.0,1.0,0.9,0.8]`.
- Costs: gym floor rental or membership $100-$400/mo, certification renewal, liability
  insurance, scheduling software $20-$60/mo, gear, protein/snack runs.

### Trade: Tutor / online teacher
- Income: weekly lessons $40-$90/hour, 8-25 hours/week, paid by Venmo/Zelle/PayPal or via a
  platform payout (weekly). School-year pattern: `[1.0,1.0,1.05,1.05,1.0,0.5,0.4,0.6,1.1,1.1,1.1,0.8]`.
- Costs: software (video, whiteboard), workbooks and printing, internet, coffee shops.

### Trade: Owner-operator trucker
- Income: `ach_client` weekly settlement from a carrier or broker (often Friday), $3,500-$8,000
  gross per week; a factoring company deducts 2-4%. Seasonality: Q4 peak
  `[0.85,0.9,1.0,1.0,1.05,1.05,1.0,1.0,1.05,1.1,1.15,1.1]`.
- Costs: fuel 25%-35% of gross (diesel fill $300-$700) mostly paid through one weekly ACH to a
  fuel-card company rather than dozens of out-of-state pump rows; truck payment $1,800-$3,200/mo;
  commercial insurance $1,000-$1,700/mo; maintenance; tolls; meals on the road; ELD/phone.
- Net left over after costs is often only 10%-30% of gross.

### Trade: Short-term rental host (Airbnb / vacation rental)
- Income: `platform_payout` per stay, 3-10 stays/month at $250-$1,400, arriving ~1-4 business
  days after check-in; platform host fee ~3% is netted. Seasonality varies by place (beach:
  `[0.6,0.6,0.8,1.0,1.2,1.5,1.6,1.5,1.1,0.9,0.7,0.8]`).
- Costs: mortgage or lease $1,100-$3,500, cleaner per stay by Zelle ($80-$150), utilities,
  supplies (Amazon), lock/hardware, property tax (semi-annual), insurance, software.

### Trade: Content creator (YouTube / Twitch / newsletter)
- Income: `platform_payout` monthly, around the 21st-26th for ad revenue; amount varies
  +/-30% month to month ($300-$4,000); sponsorships by `ach_client` $500-$5,000, net-30 or
  net-60; sometimes PayPal. Seasonality: Q4 ad rates spike `[0.8,0.85,0.9,0.95,0.95,1.0,1.0,1.0,1.05,1.1,1.25,1.4]`.
- Costs: equipment (B&H, Amazon), software and editing subscriptions, editor paid by Zelle or
  PayPal ($150-$600 per video), coworking, internet.

### Trade: Independent consultant / 1099 contractor
- Income: 1-3 clients; monthly `ach_client` retainer on or near the 1st or net-30 invoices;
  $3,000-$9,000 per month per client; low volatility except contract gaps. Seasonality ~flat
  `[0.95,1.0,1.0,1.0,1.0,1.0,0.95,0.9,1.05,1.05,1.0,0.95]`.
- Costs: health plan, LLC annual fee, SEP-IRA contribution (lump), accounting, software, travel.

## 5. Red flags an auditor should catch

- Salary-like deposits: biweekly identical amounts, `PAYROLL`, `DIRECT DEP`, W-2 withholding.
- All income on the same weekday and amount for a trade with variable earnings.
- No estimated tax payments for a persona netting over ~$30k/yr, or a payment in an off month.
- A Texas/Florida/Washington/Nevada/Tennessee/Wyoming/South Dakota/Alaska/New Hampshire resident
  with state income-tax estimates.
- Rideshare driver with zero fuel rows; Etsy seller with no postage; trucker paying 30 separate
  out-of-state pump rows; tutor income in July at the school-year peak.
- Platform payouts with round cents, or platform payouts of a size the trade could not produce.
- Closing balance far above 3 months of expenses for a low-earning gig worker.
- Zelle deposits from corporate names, or client ACH credits that look like consumer Zelle.

## Sources

- https://therideshareguy.com/when-do-gig-workers-get-paid/
- https://www.thebudgetdiet.com/doordash-fast-pay
- https://thegrocerystoreguy.com/can-you-get-paid-instantly-with-instacart/
- https://help.etsy.com/hc/articles/360046998234
- https://support.upwork.com/hc/en-us/articles/211063818
- https://www.uplisting.io/blog/how-airbnb-payment-works-new-hosts
- https://squareup.com/help/us/en/article/5438
- https://eco.com/support/en/articles/17345350-venmo-fees-2026-instant-transfer-card-and-business-charges
- https://www.kiplinger.com/taxes/tax-deadline/602538/when-estimated-tax-payments-due
- https://www.taxpayeradvocate.irs.gov/news/tax-tips/your-tax-to-do-list-important-tax-dates/2026/
- https://www.everlance.com/blog/quarterly-taxes-for-1099-vs-w2-self-employment-tax
- https://ramseysolutions.com/taxes/states-with-no-income-tax
- https://www.overdriveonline.com/partners-in-business-2019-manual-chapter-3-understanding-your-revenue-and-costs
- https://truenorth.com/articles/business/why-owner-ops-fail-high-operating-costs
- https://www.bizbuysell.com/learning-center/revenue-profit-benchmarks/hair-salons-and-barber-shops
- https://www.bizbuysell.com/learning-center/revenue-profit-benchmarks/landscaping-and-yard-service/
