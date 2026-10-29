# How Americans actually spend: frequencies, ticket sizes, taxes, tips, credit cards

Use this to build the discretionary merchant pool, the fixed bills, the ATM habit and the card
behaviour of a persona. Every number is a range because real people differ; pick inside the
range and keep a persona consistent with their income, city and household.

## 1. Big-picture budget shares (BLS Consumer Expenditure Survey, 2024)

- Average US consumer unit spent about $78,500 a year (about $6,500/month):
  housing 33.4%, transportation 17.0%, food 12.9% (about $10,170/yr, $847/mo), healthcare 7.9%.
  The rest: insurance and pensions, entertainment, apparel, personal care, education, cash gifts.
- Housing + transportation is half of household spending. Low earners spend more of their income
  on housing (35%-50%); high earners spend a smaller share.
- Monthly outflow bands (checking + card combined):
  - Lean gig worker, single: $1,800-$2,800.
  - Middle income, single or couple: $3,000-$5,500.
  - Comfortable household with kids: $6,000-$12,000.
- Fixed bills (rent, insurance, phone, utilities, loans, subscriptions) should be 55%-75% of
  outflow for lean personas, 40%-55% for comfortable ones; the rest is discretionary rows.

## 2. Discretionary merchant guide (per month for one adult unless noted)

| Category | Visits/month | Ticket | Pricing | Taxable | Tip | Typical account |
|---|---|---|---|---|---|---|
| Grocery, supermarket | 5-9 (1.6 trips/week/person) | $12-$45 fill-in, $70-$180 main shop | basket | food mostly no | no | credit or debit |
| Discount / big box | 2-4 | $25-$140 | basket | yes (non-food) | no | either |
| Warehouse club | 1-2 | $90-$350 | basket | partly | no | credit |
| Fast food counter | 3-10 | $8-$16 per person | fixed_menu | yes | no | debit/credit |
| Coffee / bakery counter | 4-12 | $3.50-$9.50 | fixed_menu | yes | optional ~$1 | debit |
| Casual sit-down dining | 1-5 | $25-$90 | basket | yes | yes | credit |
| Food delivery apps | 0-5 | $22-$65 (incl. fees, tip) | basket | yes | in total | credit |
| Gas station (fuel) | 2-6 | $25-$70 | fuel | no (tax in price) | no | credit/debit |
| Pharmacy | 1-3 | $6-$60 | basket | partly | no | debit |
| Home improvement | 0-2 | $20-$220 | basket | yes | no | either |
| Amazon / online retail | 2-7 | $12-$120 | basket | yes in most states | no | credit |
| Rideshare (rider) | 0-6 | $9-$40 + tip | basket | yes | in app | credit |
| Clothing / shoes | 0-2 | $25-$150 | basket | yes | no | credit |
| Entertainment (movies, events) | 0-3 | $14-$90 | fixed_menu | yes | no | credit |
| Haircut / personal care | 1 | $20-$85 + tip | fixed_menu | no | yes ~15%-20% | debit |
| Medical copay / dentist | 0-2 | $20-$150 | fixed_menu | no | no | debit |
| Pet supplies / vet | 0-2 | $25-$250 | basket | partly | no | credit |

- Merchants follow a power law: the top 3 merchants account for 40%-50% of discretionary rows
  (usually one grocery store, one coffee/fast-food place, one fuel brand); about 10%-15% of rows
  are one-off merchants. The pool should have 10-25 merchants, not 50.
- Weekend bias: groceries, dining and entertainment skew to Saturday/Sunday (`weekendBias` 0.5-0.8);
  coffee/fast food to weekdays (0.1-0.3); fuel is neutral.
- Max about 1-2 purchases a day at the same merchant (coffee twice in a day is plausible; a
  supermarket twice in a day is rare; gas twice in a day is a red flag).
- Travel/out-of-town merchants: no more than 5%-10% of rows, and clustered into trips of 2-6 days.

## 3. Amount rules by pricing kind

- Basket (groceries, big box, online): amounts are NOT round. Cents roughly uniform 00-99
  (a .00 total occurs under 3% of the time). Add sales tax to taxable items.
- Fuel: amount = gallons x price per gallon, rounded to cents. Gallons 8-16 per fill (the engine's range; bigger tanks are rarely
  filled from empty); price from researched facts for the region (2026 US range about
  $2.80-$4.10; California/Hawaii/Washington $1+ higher; Gulf states lower). Same station same
  week: within $0.15 per gallon. A fill of exactly $40.00 or $50.00 is a prepay/pay-at-pump
  edge case: under 5% of fill-ups.
- Fixed menu (coffee, fast food, haircuts): the same order repeats: same dollar amount within
  a few cents (tax changes) for the regular drink; small variations when someone adds an item.
- Subscriptions: constant amount; price endings .99 or .49; yearly renewals $19.99-$139.
- Amazon: 4-6 distinct amounts a month, odd cents; Prime $14.99/month or $139/year, tax-free.
- Never use the same non-subscription amount on two different merchants in a month.

## 4. Sales tax

- Base state rates (approx.): TX 6.25%, CA 7.25%, NY 4.0%, FL 6.0%, WA 6.5%, IL 6.25%, PA 6.0%,
  OH 5.75%, GA 4.0%, NC 4.75%, MI 6.0%, AZ 5.6%, CO 2.9%, MA 6.25%, NJ 6.625%, VA 5.3%,
  TN 7.0%, MN 6.875%. Combined state + local is about 6%-10% in most cities; New Orleans and
  parts of Chicago/Seattle/LA exceed 10%.
- No statewide sales tax: Oregon, Montana, New Hampshire, Delaware, Alaska (local only).
- Groceries (unprepared food) are exempt or reduced in most states; prepared food, restaurants,
  clothing (some states exempt), electronics and household goods are taxed. Gas is taxed
  through excise built into the price, not a separate sales tax line.
- No separate tax rows on a statement: tax is inside the total. For a taxable restaurant meal
  of $42.17 subtotal at 8% tax plus 20% tip on subtotal, the card total is about $53.97 (a diner who rounds the tip up may land on $54.00).
- States with no personal income tax: AK, FL, NV, NH, SD, TN, TX, WA, WY.

## 5. Tipping norms (what ends up in the card total)

- Sit-down restaurants: 15%-20% of the pre-tax bill; 18% is the effective floor now and about
  41% of diners tip 20%+. Card statement shows the final total including tip; the tip is
  usually 15%-22% of the pre-tax subtotal. Diners who tip by rounding sometimes land on a whole-dollar
  total (restaurant rows only; up to roughly a third of them).
- Counter service: coffee shops about 39% of customers tip (small, $0.50-$2); fast food about
  22% tip; so a fast-food row should usually NOT carry a tip; a barista tip is a dollar, not 20%.
- Delivery apps: tip 15%-20% of the pre-tax, post-discount subtotal; total includes delivery
  and service fees (30%-45% above menu price all in).
- Hair stylists/barbers: 54% always tip; 15%-20% of the service price; paid on the card
  (the amount is the service + tip) or in cash (invisible).
- Rideshare: tip 10%-20%, shown as a separate or merged charge a few hours after the ride.
- Nobody tips the gas station, the grocery store or the pharmacy.

## 6. Subscriptions and fixed bills

- A typical adult has 3-7 subscriptions (streaming $8-$25 each, music $11-$12, cloud storage
  $1-$10, news, software, phone insurance, gym). Total $35-$120 per month.
- Mobile phone $35-$140 (family plans $100-$220). Home internet $50-$90.
- Auto insurance: liability-only about $98/mo, full coverage $186-$216/mo nationally in 2026,
  $90-$320 by state and driver; renters insurance $12-$25/mo; marketplace health plan $250-$700.
- Rent: national median asking rent about $1,670-$1,715 (2026); small cities $850-$1,400;
  big coastal metros $1,800-$3,200 for one bedroom. Rent is 25%-40% of income for most renters.
  It changes at most once a year (an increase of 2%-8% at lease renewal). Paid by ACH/portal
  (`APPFOLIO RENT`), check, or Zelle.
- Car loan $350-$750/mo, student loan $150-$500/mo, personal loan/BNPL occasionally.
- Gym membership: budget $10-$60, boutique $90-$220; January sign-ups common.

## 7. ATM and cash habits

- Many adults use ATMs rarely or never. A debit-heavy persona: 1-3 withdrawals per month at $20-$200 (multiples of $20), mostly in-network and fee-free.
- Out-of-network withdrawal costs about $4.86 total ($3.22 surcharge + $1.64 own-bank fee);
  record the fee as its own row right after the withdrawal.
- Cash-heavy personas (cleaners, tip-based jobs) deposit cash at ATMs: round dollar amounts.
- Zero ATM use is plausible for a young, card-only persona; 8+ withdrawals a month is not.

## 8. Credit card behaviour

- APR on purchases: 18%-29.99%, typical 21%-27%. Credit limit: $1,500-$5,000 for thin credit or
  gig workers; $5,000-$15,000 for established; utilisation 10%-40%.
- Interest: average daily balance x APR / 365 x days in the cycle. Interest appears on the
  closing day, only when the prior statement balance was not paid in full; if you pay the full
  statement balance by the due date there is no interest and the grace period holds.
- Minimum payment: the greater of about $25-$40 or about 1% of the balance plus that month's
  interest and fees; for a $2,000 balance at 24% APR about $60-$70.
- Payment strategies: `full` (statement balance, near the due date or by autopay) or `partial`
  (minimum, round numbers like $100, $150, $250, or a half). Partial payers carry balances and
  pay $30-$90 in monthly interest on $1,500-$4,000.
- Late fee: about $30-$41 on missed due date; most personas pay on time.
- Balance rows: purchases (stores, gas, subscriptions), credits (refunds), payments,
  interest and fees. A card the persona uses has 15-45 rows a month, not 3.
- Rent and tax payments are almost never on the card; gas, groceries, online, subscriptions,
  restaurants are (rewards).
- Foreign merchants add a `FOREIGN TRANSACTION FEE` of about 3% right after the charge.

## 9. Behaviour quirks that make a statement feel real

- A few things go wrong: one small unfamiliar charge, one refund, a duplicate-looking pair a day
  apart (two coffees), a late subscription renewal.
- Tight months: fewer dining rows, late non-priority bills, maybe one overdraft fee (always exactly $35.00 in this product).
- Gig personas keep a floor balance ($300-$1,500) and a cushion for
  taxes; transfers from savings into checking are plausible but rare (type TRANSFER).
- Life events (holiday travel, car repair $300-$1,500, medical $50-$400, a new phone) appear
  as one-off rows, not repeated monthly.
- Cash back, interest earned and small refunds are tiny credits ($0.40-$18).

## 10. Red flags an auditor should catch

- Round-dollar grocery, gas or restaurant totals as the norm; identical amounts on different
  days at a grocery store.
- Tip on a fast-food or coffee counter purchase at 18%-20%; no tip pattern at a sit-down place.
- Seven fill-ups a month for a non-driver or zero fuel for a driver; two fills the same day.
- Sales-tax-free purchases of taxable goods in a high-tax city, or tax on baby formula.
- Netflix or other subscription at a different price each month, or on different days.
- Persona spending far above income without a credit line; a card balance that never changes.
- Interest charged on a card paid in full; payment equal to the whole balance while interest
  still accrues from the earlier cycle.
- ATM withdrawals of $87 or $143; out-of-network fee missing; foreign fees without foreign merchants.
- 60 distinct merchants in a month for a 25-purchase persona; one-off merchants dominating.
- Rent on the card; rent amount that changes each month.

## Sources

- https://www.bls.gov/opub/reports/consumer-expenditures/2024/
- https://www.bls.gov/opub/ted/2026/housing-and-transportation-accounted-for-50-percent-of-household-spending-in-2024.htm
- https://www.bls.gov/news.release/archives/cesan_12192025.pdf
- https://chainstoreage.com/study-shoppers-still-value-store-grocery-shopping-heres-why
- https://www.ers.usda.gov/data-products/charts-of-note/79653
- https://moneylion.com/trending/money/what-americans-actually-tip-in-2026-from-coffee-shops-to-cabs
- https://www.inquirer.com/consumer/tipping-culture-us-consumers-banned-wallethub-survey-20260313.html
- https://www.bankrate.com/banking/checking/checking-account-survey/
- https://www.bankrate.com/press-releases/atm-fees-hit-record-high-for-third-consecutive-year/
- https://www.nerdwallet.com/blog/credit-cards/how-credit-card-interest-calculated
- https://wtop.com/news/2016/09/how-credit-card-companies-calculate-interest
- https://www.insurify.com/car-insurance/average-car-insurance-cost/
- https://www.baselane.com/resources/average-rent-statistics
- https://fred.stlouisfed.org/series/GASREGM
- https://ramseysolutions.com/taxes/states-with-no-income-tax
- https://finance.yahoo.com/news/15-states-no-income-tax-200001227.html
