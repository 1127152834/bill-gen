# US calendar, bank days and seasonality

Real statements follow the calendar: banks close, ACH skips weekends, bills land on fixed days,
and spending swells with weather and holidays. Use this for dates, month-by-month multipliers
and weekday patterns. Multipliers below average about 1.0 over 12 months; index 0 = January.

## Mapping to the model

Recurring `dayRule` supports only `dayOfMonth` (1-28), `taxDates` and `biweekly {weekday, anchor}`.
"Last day of month" -> 28; weekly recurring items -> discretionary-pool entries (`perMonth` 4-5);
semi-monthly -> two `dayOfMonth` items (15 and 28). Income cadence: weekly, biweekly, monthly, irregular.

## 1. Bank business days (the engine posts only on these for ACH)

- Business days are Monday-Friday except Federal Reserve holidays. ACH credits/debits, bill pay,
  Zelle-to-bank settlement of business payouts, payroll and tax payments never post on a
  Saturday, Sunday or Fed holiday. They post the next business day.
- Card purchases can happen on any day. Their `transactionDate` is the swipe day; the
  `postedDate` is 0-3 days later (weekend/holiday swipes post the next business day; gas pumps
  and restaurants sometimes a day later). Debit-card rows on checking usually post within 1 day.
- A payout, client ACH or bill debit scheduled for a weekend or Fed holiday posts on the NEXT
  business day (the engine moves it forward; it never lands early).
- Mobile deposit: counted the same business day if before cutoff, else the next. The first $275
  of a check is available the next business day, the rest on the second; large and new-account
  deposits can be held longer.

Federal Reserve holidays (when a holiday falls on a Saturday, Reserve Banks stay open the
preceding Friday; when on a Sunday they close the following Monday):

| Holiday | 2025 | 2026 | 2027 |
|---|---|---|---|
| New Year's Day | Wed Jan 1 | Thu Jan 1 | Fri Jan 1 |
| Martin Luther King Jr. Day | Mon Jan 20 | Mon Jan 19 | Mon Jan 18 |
| Washington's Birthday | Mon Feb 17 | Mon Feb 16 | Mon Feb 15 |
| Memorial Day | Mon May 26 | Mon May 25 | Mon May 31 |
| Juneteenth | Thu Jun 19 | Fri Jun 19 | Sat Jun 19 (open Fri Jun 18) |
| Independence Day | Fri Jul 4 | Sat Jul 4 (open Fri Jul 3) | Sun Jul 4 (closed Mon Jul 5) |
| Labor Day | Mon Sep 1 | Mon Sep 7 | Mon Sep 6 |
| Columbus Day | Mon Oct 13 | Mon Oct 12 | Mon Oct 11 |
| Veterans Day | Tue Nov 11 | Wed Nov 11 | Thu Nov 11 |
| Thanksgiving | Thu Nov 27 | Thu Nov 26 | Thu Nov 25 |
| Christmas | Thu Dec 25 | Fri Dec 25 | Sat Dec 25 (open Fri Dec 24) |

Not bank holidays (banks open, retail changes): Easter (Sunday), Mother's Day (2nd Sunday of May:
May 11, 2025; May 10, 2026), Father's Day (3rd Sunday of June), Halloween, Black Friday (day after
Thanksgiving: Nov 28, 2025; Nov 27, 2026), Cyber Monday (next Monday), Valentine's Day.

## 2. Fixed-date money events

- Rent/mortgage: due the 1st; paid 1st-5th (most by the 1st, 2nd or 3rd). A rent debit on the
  17th is a red flag. If the 1st is a weekend, the debit/Zelle is the Friday before or Monday after.
- Federal estimated taxes: Apr 15, Jun 15, Sep 15, Jan 15. 2025: Apr 15 (Tue), Jun 16 (Mon; Jun 15
  was Sunday), Sep 15 (Mon), Jan 15 2026 (Thu). 2026: Apr 15 (Wed), Jun 15 (Mon), Sep 15 (Tue),
  Jan 15 2027 (Fri). 2027: Apr 15 (Thu), Jun 15 (Tue), Sep 15 (Wed). Payments may be made a few
  days early; never more than ~10 days late without a penalty notice.
- Business filings: payroll tax deposits follow payroll; quarterly payroll returns end Apr 30,
  Jul 31, Oct 31, Jan 31; state sales tax usually due the 20th-25th of the next month (monthly)
  or after quarter end; property tax in 1-2 installments (often Nov/Dec and Apr/May).
- Utilities: bill cycle is fixed, auto-pay day varies by 0-2 days with weekends; amount varies.
- Subscriptions: charge on the same day of the month each time (sign-up day); shortened months
  (29th-31st) fall back to the last day; in the model use day 28 at most. A subscription on random days is a red flag.
- Insurance: monthly auto-pay on a fixed day, or 6-month premium on the renewal date.
- Credit card: statement closes on a fixed day (any day 1-28); payment due about 21-25 days later;
  card payment from checking usually lands 1-5 days before the due date or on autopay day.
- Tax refund (`IRS TREAS 310 TAX REF`): February 15 to mid-April for most filers.
- Business payroll: model it as `biweekly` on a fixed weekday (every other Friday is most common);
  semi-monthly payroll is two `dayOfMonth` items on day 15 and day 28 (shift to the preceding business
  day). The engine has no weekly payroll rule and no day 29-31 or "last day" rule.
- Personal 1099 personas have no payday; avoid a "1st and 15th" deposit rhythm.

## 3. Seasonal multipliers for bills (use for `seasonal` amounts)

| Item | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Electric, hot-summer South | 0.85 | 0.8 | 0.8 | 0.85 | 1.0 | 1.25 | 1.4 | 1.4 | 1.2 | 0.95 | 0.8 | 0.7 |
| Electric, temperate/North | 1.05 | 1.0 | 0.9 | 0.85 | 0.85 | 1.0 | 1.2 | 1.2 | 1.0 | 0.85 | 0.9 | 1.1 |
| Natural gas, cold climate | 1.9 | 1.7 | 1.4 | 0.9 | 0.55 | 0.4 | 0.35 | 0.4 | 0.5 | 0.8 | 1.3 | 1.8 |
| Natural gas, mild climate | 1.7 | 1.5 | 1.2 | 0.9 | 0.7 | 0.6 | 0.55 | 0.55 | 0.65 | 0.9 | 1.3 | 1.5 |
| Water / sewer | 0.9 | 0.9 | 0.95 | 1.0 | 1.05 | 1.15 | 1.2 | 1.2 | 1.05 | 1.0 | 0.9 | 0.9 |

- Electricity peaks June-August because of air conditioning (about 87% of US homes have A/C,
  roughly 17% of annual residential electricity spending); lowest in April-May and October.
  National average summer bill was about $150-$190 in 2020-2025 (about 1,100 kWh/month).
  Use `peakMonths [6,7,8]` for A/C regions, `[12,1,2]` for gas heat, `[1,2,12]` for electric heat.
- Typical monthly ranges: electric $70-$300 (summer South up to $350), gas $15-$220 (winter
  peak), water/sewer $35-$120, trash $15-$40, internet $50-$90 flat, mobile phone $35-$140 with
  small taxes/fees drift (+/- $2-$6), streaming $8-$25 each flat.
- Heating oil/propane: 2-5 deliveries a year in cold months ($250-$900 each), not monthly.
- Gas prices: pump price moves a few cents per week, up in spring and summer, down in autumn.
  Prices of 2026 swung from about $2.81 in January to about $3.6-$4.1 in spring and summer.
  Two stations in one city within a week differ by under about $0.25 per gallon. Take the
  price from researched merchant facts when available.

## 4. Seasonal multipliers for spending

| Pattern | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Discretionary spending | 0.90 | 0.88 | 0.95 | 0.98 | 1.02 | 1.02 | 1.08 | 1.02 | 0.97 | 0.98 | 1.05 | 1.25 |
| Dining out | 0.9 | 0.9 | 1.0 | 1.0 | 1.05 | 1.05 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.1 |
| Travel (air/hotel) | 0.7 | 0.7 | 0.9 | 0.95 | 1.05 | 1.35 | 1.45 | 1.25 | 0.85 | 0.85 | 0.95 | 1.1 |
| Online shopping (Amazon etc.) | 0.9 | 0.85 | 0.9 | 0.9 | 0.95 | 1.0 | 1.15 | 1.0 | 0.95 | 1.05 | 1.3 | 1.55 |
| Groceries | 1.0 | 0.95 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.98 | 0.98 | 1.0 | 1.1 | 1.12 |
| Fuel (gallons) | 0.9 | 0.9 | 1.0 | 1.0 | 1.05 | 1.1 | 1.1 | 1.1 | 1.0 | 1.0 | 0.95 | 0.95 |

- Nov-Dec holiday retail: about 19% of annual retail sales falls in those two months. Gift buys
  concentrate Nov 15-Dec 23; Black Friday weekend and Cyber Monday are the biggest single days.
- Amazon sales events: July (Prime Day) and October; first half of July sees a bump in Amazon
  rows. Back-to-school (late July-early September): school supply, clothing, electronics.
- Spring: Mother's Day gifts (week before the 2nd Sunday of May); graduation gifts in May-June;
  Easter (March/April) lifts groceries; Valentine's Day (Feb 10-14) lifts restaurants/flowers.
- Thanksgiving week: groceries 1.3-1.8x for the shopper, restaurants quiet on the day itself.
- Winter: January is the post-holiday trough; gyms and subscriptions are sign-up heavy; heat bills peak.
- Summer: travel, entertainment, rideshare and ice cream up; school-year income (tutors) down.
- Tax season (Feb-Apr): accountants/software (`TURBOTAX`, `H&R BLOCK`) charges for filers.

## 5. Day-of-week patterns (transaction dates)

| Category | Heavy days | Light days |
|---|---|---|
| Groceries | Sat, Sun, Wed | Mon, Tue |
| Coffee, fast food (workday) | Mon-Fri 6-10 AM and noon | Sun |
| Restaurants (sit-down) | Fri, Sat dinner; Sun brunch | Mon, Tue |
| Fuel | Fri, Sun, commute days | - |
| Entertainment, bars, rideshare | Fri, Sat night | Mon-Wed |
| Pharmacy, medical, DMV | Mon-Fri | Sun |
| Costco / big box | Sat, Sun | weekday morning |
| Amazon / online | any day, peak Sun-Mon evenings | - |
| Home improvement | Sat, Sun, spring/fall | - |

- Never put a bank or insurance office visit, medical copay or hair salon on a Sunday for a
  typical persona; most salons are closed Sunday-Monday.
- Business sales: restaurants and bars strongest Friday and Saturday; retail Saturday; services
  Monday-Friday.
- Weekend card purchases post on Monday; so Monday posted rows may be 2-3 transaction dates.

## 6. Statement cycle

- Checking statements normally run calendar months (1st-last day). Credit statements close on
  one fixed day each month (the "closing day" in the model, 1-28) and the interest and
  payment rows follow that cycle.
- First and last days: partial cycles contain proportional rows. A 3-month request should still
  include one rent, one of each subscription per cycle, and at least one estimated tax in the
  right window if the window covers a tax date.
- Fees: monthly service fee lands on the same `dayOfMonth` (1 or 28) each cycle.

## 7. Red flags an auditor should catch

- ACH, payroll, tax or bill-pay rows posted on Saturday, Sunday or a Fed holiday.
- Rent on a random day, or paid twice a month, or missing in a month.
- Estimated tax in March or July, or four payments on dates unrelated to Apr 15, Jun 15,
  Sep 15, Jan 15.
- Electric bill highest in April; gas heating peak in July; identical utility amounts each month.
- December gift spending absent; gym sign-up in July; Prime Day pattern in February.
- Black Friday online surge on the wrong date; Thanksgiving Day grocery rows; restaurant rows
  on Christmas Day for most personas.
- Fuel price identical to the cent on every fill-up, or off by > $1 from the region.
- Payroll on random days; sales tax paid weekly.
- A subscription that charges on different days of the month each time.

## Sources

- https://www.frbservices.org/about/holiday-schedules
- https://www.atlantafed.org/who-we-are/bank-holidays
- https://www.centralbank.com/assets/pdfs/federal-reserve-holidays-2026.pdf
- https://www.kiplinger.com/taxes/tax-deadline/602538/when-estimated-tax-payments-due
- https://www.taxpayeradvocate.irs.gov/news/tax-tips/your-tax-to-do-list-important-tax-dates/2026/
- https://www.eia.gov/todayinEnergy/detail.php?id=35812
- https://www.eia.gov/outlooks/steo/pdf/SF02.pdf
- https://eprinc.org/wp-content/uploads/2026/07/EPRINC-Chart2025-23-USResidentialSummerElectricityTrends.pdf
- https://www.epa.gov/sites/production/files/2021-04/documents/residential_energy_use_-_td.pdf
- https://nrf.com/research-insights/holiday-data-and-trends/winter-holidays/winter-holiday-faqs
- https://fred.stlouisfed.org/series/GASREGM
- https://www.rigzone.com/news/eia_cuts_2026_2027_usa_gasoline_price_projection-10-jul-2026-184110-article/
- https://www.websterfirst.com/blog/funds-availability-rules-explained-regulation-cc/
- https://support.gusto.com/article/186733245100000/processing-payroll-overview-for-admins
