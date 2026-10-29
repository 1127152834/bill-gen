# Small-business cost structures by industry

Use this to turn a business persona (type, size, city) into a believable bank account: how much
comes in, from which processors, and where it goes. Percentages are shares of gross revenue
for a healthy independent single-location business. A statement shows CASH movements, so each
cost line becomes real debits: suppliers, payroll runs, rent, insurance, loans, taxes.

## 1. Business-wide rules

## Mapping to the model (what the engine can express)

- Recurring items (`dayRule`): `dayOfMonth` 1-28 only (days 29-31 do not exist: use 28), `taxDates`,
  or `biweekly {weekday, anchor}`. There is no weekly or "last day of month" rule.
- Payroll -> `biweekly` (the common small-business choice, e.g. every other Friday). Semi-monthly
  payroll -> two `dayOfMonth` items, day 15 and day 28. Never "weekly" payroll.
- Weekly deliveries and weekly vendor orders (food distributor, produce, dairy, parts, fuel card)
  -> discretionary-pool entries (`perMonth` about 4-5 for weekly, 8-10 for twice a week), not recurring items.
- Monthly vendor/loan/rent/insurance/subscription -> recurring `dayOfMonth`. Weekly rent (booth
  rent) -> monthly amount on one `dayOfMonth`.
- Income cadence: `weekly`, `biweekly`, `monthly` (day 1-28) or `irregular {perMonth}`. Daily card
  batches -> `irregular` with `perMonth` about 18-26 (open days); the engine places them on business days.
- Prose below that says "weekly" or "last day" describes reality; always translate it as above.

- Money in: `card_sales` batches from the POS processor on business days (net of 2.6%-3.5%
  fees), plus checks, ACH from corporate/B2B customers, Zelle/Venmo from small customers.
  Weekend sales land on Monday/Tuesday, so Monday deposits are the largest.
- Sales collected includes sales tax; the tax is remitted separately (monthly or quarterly,
  around the 20th, to the state revenue department). Only taxable states/goods; groceries and
  most services are often exempt. States with no sales tax: OR, MT, NH, DE, (AK has local only).
- Businesses DO pay employees. Payroll through Gusto/ADP appears as separate ACH debits:
  net pay (`GUSTO NET`) about 75%-85% of gross wages, taxes (`GUSTO TAX`) about 20%-30% of gross
  wages (withholding plus employer share), fee (`GUSTO FEE`) about $45-$60 base + ~$6-$12 per
  employee per month. Debit lands 1-4 business days before payday. Biweekly, always on
  the same weekday (Friday payday is most common; semi-monthly = two items on day 15 and 28). Owner of a sole-prop/LLC is NOT on payroll:
  paid by `OWNER DRAW` transfer or Zelle to a personal account, irregular, 5%-15% of revenue.
- Rent: fixed amount, same day each month (1st-5th), priority 1; commercial rent typically
  $1,200-$9,000/month for small spaces; plus utilities and insurance.
- Loans (equipment, SBA, vehicle): fixed amount, same day, ACH, priority 1.
- Insurance: general liability $50-$250/mo; workers' comp monthly auto-pay once there are
  employees; commercial auto if vehicles.
- Net margin check: revenue minus all debits should leave 3%-20% (industry-dependent). An
  account that nets 40% or loses money every month is not plausible for a healthy shop.
- Opening balance: 3-6 weeks of fixed costs ($4,000-$40,000 for most small shops); never round.
- Peak months have 1.2-2.0x the revenue of slow months; supplier debits follow revenue
  with a lag of 1-4 weeks.
- Vendor debits use bill-pay/ACH/check; small supplies by debit card at warehouse stores.

## 2. Industries

### Industry: Full-service restaurant
- Revenue: `card_sales` daily (Toast/Square/Clover). $60,000-$250,000/month for 50-80 seats;
  average check $18-$45 per person; Friday/Saturday 1.4x, Monday often closed.
- Costs: food + beverage 28%-35%; labor 25%-35% (prime cost food + labor 55%-65%);
  rent/occupancy 6%-10%; utilities 3%-5%; other 8%-12%; net 3%-9%.
- Debits: food distributor (`SYSCO`, `US FOODS`) about 4-9 a month, produce 8-12 a month, bar/liquor
  about 4 a month (all discretionary pool), linen/uniform, grease trap, POS fees monthly,
  payroll biweekly, sales tax monthly.
- Seasonality: `[0.9,0.9,1.0,1.0,1.05,1.05,1.0,1.0,1.0,1.0,1.0,1.1]`; holiday week spikes.
- Tips on cards are paid out to staff via payroll; do not show them as owner income.

### Industry: Cafe / coffee shop
- Revenue: `card_sales` daily; 150-400 transactions/day at an average ticket $4-$9 (about $6);
  $18,000-$60,000/month.
- Costs: COGS 25%-30% (beans, milk, syrups, cups); labor 25%-35%; rent 8%-15%; net 5%-15%.
- Debits: dairy about 8 a month, roaster 2-4 a month ($300-$900), bakery deliveries, cups and
  supplies monthly, POS fees, payroll biweekly, rent, utilities, insurance.
- Seasonality: `[1.0,1.0,1.0,1.0,1.0,0.95,0.9,0.9,1.05,1.05,1.05,1.1]` (college towns drop in
  summer; iced drinks lift Jun-Aug elsewhere). Open early, so deposits include Saturday/Sunday.

### Industry: Retail boutique
- Revenue: `card_sales` daily; ticket $45-$120; $15,000-$60,000/month.
- Costs: COGS about 50% (keystone markup doubles cost); rent 8%-12%; labor 12%-20%; net 4%-8%.
- Debits: wholesale inventory buys (ACH/card) 2-6x/month, bigger in Jan/Jul (markets), shipping,
  Shopify/Square fees, rent, payroll biweekly, signage/advertising.
- Seasonality: `[0.7,0.75,0.9,1.0,1.1,1.0,0.9,1.0,0.95,1.0,1.5,2.0]`; Nov-Dec is around 19% of
  annual retail sales; inventory purchases peak Sep-Oct.

### Industry: E-commerce store (Shopify / Amazon seller)
- Revenue: Stripe/Shopify/PayPal payouts every 1-3 business days; $8,000-$90,000/month;
  average order $35-$120. Payouts are net of 2.9% + $0.30 processing.
- Costs: COGS 40%-50%; shipping 10%-15%; advertising 15%-25%; returns 3%-5%; platform
  5%; net 5%-15%.
- Debits: supplier ACH/wire (batches $2,000-$25,000, monthly), ad spend by card
  (`FACEBK *ADS`, `GOOGLE *ADS`, daily or threshold billing), shipping `USPS`/`UPS`/
  `PIRATE SHIP`, apps and software, 3PL invoices.
- Seasonality: `[0.8,0.8,0.85,0.9,0.95,0.9,0.9,1.0,1.0,1.15,1.6,1.8]`.

### Industry: Hair salon / barbershop
- Revenue: `card_sales` daily (Square/Fresha/Booksy); ticket $25-$120 with tips; $15,000-$50,000
  per month for 3-6 chairs. Some chairs are booth renters paying the owner weekly/monthly.
- Costs: labor/commission 40%-55%; rent 8%-15%; supplies 3%-12%; net 10%-20%.
- Debits: payroll biweekly (or commission payouts by Zelle), beauty distributors 2-4x/
  month, booking software $30-$100/mo, utilities, laundry, licence fees.
- Seasonality: `[0.85,0.95,1.0,1.05,1.1,1.0,0.95,1.05,1.0,1.0,1.05,1.2]`.

### Industry: Auto repair shop
- Revenue: card batches plus some checks and fleet ACH; ticket $150-$1,200; $40,000-$150,000/mo.
  Labor rates $120-$159/hour; parts gross margin 51%-60%.
- Costs: parts about 25%-30% of total revenue; technician pay 25%-35%; rent 5%-10%;
  net 6%-7% typical, 15%-20% well-run.
- Debits: parts suppliers (`NAPA AUTO PARTS`, `ADVANCE AUTO`, tire distributors) about 12-20 a month,
  shop software, waste oil/tire disposal, tools, payroll biweekly, insurance.
- Seasonality: `[0.9,0.9,1.0,1.05,1.05,1.05,1.0,1.0,1.05,1.05,1.0,0.95]`.

### Industry: Landscaping / lawn company with employees
- Revenue: residential card/ACH/check, commercial contracts monthly ACH; seasonal
  $15,000-$120,000/month; recurring weekly routes.
- Costs: labor 25%-50% (average about 35%); fuel and equipment 8%-12%; net 8%-14%.
- Debits: payroll biweekly (crews), fuel (card, or about 4 fuel-card ACH debits a month), mulch/stone/
  nursery suppliers, equipment parts, truck and trailer loans, insurance, dump fees.
- Seasonality (cold): `[0.2,0.25,0.6,1.1,1.4,1.4,1.3,1.3,1.3,1.1,0.5,0.25]`; fixed costs continue
  in winter, payroll stops for seasonal crew.

### Industry: General contractor / remodeler
- Revenue: progress draws and deposits by check/ACH/wire; 1-6 deposits/month of
  $8,000-$80,000, arriving 30-60 days after the work or invoice (deposit up front 10%-30%).
- Costs: materials 25%-40%; subcontractors 20%-35%; direct labor; overhead 15%-25%;
  net 5%-10%.
- Debits: lumber yard and supply house (about 4-8 a month), subs by check/ACH/Zelle on the same day or
  a few days after the draw arrives, payroll biweekly, permits, workers' comp, truck loans.
- Cash-flow squeeze: material and payroll debits precede deposits; keep a larger opening
  balance and a business credit card.
- Seasonality: `[0.7,0.75,0.9,1.05,1.15,1.2,1.2,1.2,1.1,1.1,0.9,0.75]`.

### Industry: HVAC / plumbing contractor
- Revenue: card payments at job completion (via Stripe/Square/field-service software),
  financing-partner payouts, commercial ACH; ticket $180-$9,000; $60,000-$250,000/month for 4-8 techs.
  Revenue per technician about $150,000-$350,000/year.
- Costs: gross margin 40%-60% (service 48%-58%); labor 22%-30%; fleet and fuel 5%-8%; net
  median about 6%, top quartile about 13%.
- Debits: equipment/parts supply house about 4-8 a month, fuel-card ACH about 4 a month, truck
  loans, payroll biweekly, licence and insurance, software.
- Seasonality: HVAC `[1.0,0.9,0.85,0.85,1.0,1.3,1.5,1.4,1.0,0.85,0.9,1.1]`; plumbing flatter.

### Industry: Fitness studio / gym
- Revenue: recurring memberships billed by Stripe/studio software on set days (1st and 15th
  are common), plus drop-ins and class packs; $15,000-$80,000/month; $60-$200 per member.
- Costs: payroll/instructors 30%-40%; rent 15%-20%; software and marketing 3%-6%; net
  15%-30% for healthy studios.
- Debits: rent, instructor payroll or contractor ACH, music licensing, software, equipment
  repair, laundry, insurance, utilities.
- Seasonality: `[1.3,1.1,1.0,0.95,0.95,0.9,0.9,0.9,1.05,1.0,0.95,0.85]`; January surge, summer dip.

### Industry: Cleaning service company
- Revenue: card on file via Stripe/Square/booking software, ACH from commercial clients;
  $12,000-$60,000/month; recurring weekly/biweekly visits.
- Costs: labor 40%-55%; supplies 4%-6%; vehicle and mileage; insurance; net 15%-25%.
- Debits: payroll biweekly, supplies (warehouse stores), fuel, booking software,
  liability insurance, vehicle loan.
- Seasonality: `[0.95,0.95,1.05,1.15,1.0,0.95,0.9,0.9,1.0,1.0,1.05,1.15]`.

### Industry: Food truck
- Revenue: Square/Toast card sales daily on working days; events pay by ACH ($500-$3,000);
  about $950/day average, $14,000-$40,000/month. Ticket $9-$18.
- Costs: food 25%-35%; labor 20%-30% (owner often works); commissary $500-$1,500/month;
  fuel and propane $500-$1,200/mo; permits $200-$3,000/year; net 6%-9%.
- Debits: wholesale club/food distributor about 8 a month, commissary rent monthly, truck loan,
  insurance, POS fees, permits (annual lump).
- Seasonality (cold): `[0.4,0.5,0.8,1.0,1.3,1.4,1.4,1.4,1.2,1.0,0.6,0.5]`.

### Industry: Professional services (accounting, design, consulting agency)
- Revenue: `ach_client` invoices net-15 to net-45; some card payments via Stripe; retainers
  on the 1st; $15,000-$120,000/month.
- Costs: payroll 55%-70% of revenue; rent 4%-8%; software 3%-6%; insurance (E&O); net 12%-25%.
- Debits: payroll biweekly or semi-monthly (day 15 and 28; 1-4 business days before payday), rent, software
  subscriptions, health plan, accountant, travel.
- Seasonality: `[0.95,1.0,1.1,1.15,1.0,1.0,0.9,0.9,1.05,1.05,1.0,0.9]` (tax-season firms spike
  Feb-Apr).

## 3. How to size a business account

1. Pick monthly revenue from the industry range (use the persona's size). Divide into
   deposit batches: daily card batches on open days, or 2-6 invoice deposits.
2. Multiply by `seasonality` for the month. Keep the 12-month average near 1.0.
3. Allocate costs by the percentages. Payroll is `biweekly` (or two `dayOfMonth` items), rent/loans fixed
   `dayOfMonth`, suppliers as discretionary entries with lumpy amounts, sales tax monthly.
4. Compare net to the industry margin; adjust vendor frequency if it is off by > 5 points.

## 4. Red flags an auditor should catch

- A restaurant with no food-distributor debits, or card-sales deposits on closed days.
- A coffee shop with payroll but no milk/roaster/cup suppliers.
- Rent on a random day each month, or rent that changes amount every month.
- Weekly payroll, payroll on irregular weekdays, one combined payroll debit with no tax debit, or employees
  paid by Zelle while a payroll provider also appears.
- Net pay equal to gross, or tax debit of 5% of wages; the owner on payroll for a sole prop.
- Landscaper with full revenue in January; HVAC without summer or winter peaks.
- Revenue well above the industry range for the stated size; deposits with no fees netted
  (round cents from a card processor).
- Loans and rent skipped in a slow month while discretionary spending continues.
- Sales tax remitted in a no-sales-tax state (OR, MT, NH, DE) or never remitted.

## Sources

- https://restaurant.eatapp.co/blog/restaurant-metrics-for-profit
- https://www.completecontroller.com/cost-percentage-in-restaurants/
- https://bellwethercoffee.com/blog/coffee-shop-profit-margins
- https://therestaurantwarehouse.com/blogs/restaurant-equipment/coffee-shop-profit-margin
- https://www.bizbuysell.com/learning-center/revenue-profit-benchmarks/coffee-shops-and-cafes
- https://www.bizbuysell.com/learning-center/revenue-profit-benchmarks/hair-salons-and-barber-shops
- https://www.joinhomebase.com/blog/salon-monthly-expenses
- https://partstech.com/?p=6064
- https://sunbit.com/knowledge-center/auto/good-profit-margin-auto-repair-shop/
- https://www.bizbuysell.com/learning-center/revenue-profit-benchmarks/landscaping-and-yard-service/
- https://www.youraspire.com/blog/landscaping-labor-percentage-of-revenue
- https://relayfi.com/blog/general-contractor-profit-margins/
- https://simprogroup.com/blog/hvac-industry-statistics-2026
- https://www.pushpress.com/blog/busy-but-broke-why-high-revenue-gyms-can-still-lose-money
- https://maidcentral.com/2026/06/10/revenue-vs-profit-cleaning-business-2/
- https://www.getharvest.com/calculators/average-food-truck-profit-margin
- https://smallbusiness.chron.com/markup-percent-boutiques-35432.html
- https://topgrowthmarketing.com/profit-margin-for-ecommerce/
- https://support.gusto.com/article/186733245100000/processing-payroll-overview-for-admins
- https://nrf.com/research-insights/holiday-data-and-trends/winter-holidays/winter-holiday-faqs
