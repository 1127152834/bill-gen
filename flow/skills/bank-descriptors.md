# Bank statement descriptors: how real transaction lines read

Use this when writing one description template per merchant or income stream. The output is the
text a US account holder sees on a statement line. It is NOT prose: it is terse, UPPERCASE,
truncated, and full of processor codes. Realism comes from the ugly details.

## 1. Output contract (hard rules)

- Placeholders you may use: `{STORE}` (the researched store number when the fact has one, else a
  fixed 4-digit one; chains only), `{CITY}` (uppercase, max 13 chars), `{ST}` (2-letter state),
  `{MMDD}` (transaction date), `{ID}` (a 6-10 digit account / customer number, the same on every
  row of a merchant), `{REF}` (a per-row reference: 10 A-Z0-9 on card, Zelle and ATM rows; digits
  on ACH rows; the 15-digit trace on IRS payments), `{LAST4}`.
- `{REF}` is for codes that really change per transaction: Amazon order codes, Zelle confirmation
  codes, ATM terminal references, the IRS trace. Never put `{REF}` on an ACH bill (utility,
  insurance, loan, rent): real bill-pay lines repeat the same text every month, at most with a
  fixed account number (`{ID}`).
- After filling, the line is uppercased, whitespace collapsed, and cut to 60 characters. Budget
  for it: prefix + name + store + `{CITY}` (13) + `{ST}` (2) must fit. A long prefix plus a long
  name loses the city/state, which is realistic, but never cut the line mid-placeholder.
- Allowed characters: A-Z 0-9 space `# * . , - / & '`. No lowercase, no emoji, no pipes, no
  quotes around the name. Apostrophes are usually dropped or become a space: `MCDONALD S`,
  `TRADER JOES`, `LOWES`, `DENNY S`.
- Same merchant = same template on every row. `{STORE}` is fixed per merchant by the engine, so
  a customer's regular grocery store always shows the same store number.
- Write the `memo` as plain English a human would say ("Groceries at Trader Joe's"), no codes.
- Fixed keys `card-payment`, `interest`, `fee-overdraft`, `fee-atm`, `atm` have built-in
  templates (sections 10 to 12). Only override them if told to.
- Do not invent a bank name, logo, routing number or full account/card number. `{LAST4}` only.
- Pick ONE house prefix style per statement (section 3) and use it for every card row on the
  checking account. Mixing `CHECKCARD` and `POS PURCHASE` on one account is a red flag.

## 2. Card descriptor anatomy (all card purchases)

A card descriptor is what the merchant's processor sends to the card network. Limits:

| Part | Typical limit | Notes |
|---|---|---|
| Merchant name field | 22 chars (Mastercard), 25 (Visa) total | Longer names are truncated mid-word |
| City | 13 chars | `SAN FRANCISCO` becomes `SAN FRANCISC` |
| State | 2 chars | Online merchants often show their HQ state, not the buyer's |
| Store / terminal id | 3-8 digits after `#` or a space | Chains only; sole proprietors have none |

- Anatomy: `[PROCESSOR PREFIX][NAME] [#STORE] [CITY] [ST]`. Example: `TST* LUNA TACOS` + `AUSTIN TX`.
- Chains: `NAME #STORE CITY ST` (`TARGET #{STORE}`). Local independents: name + city only.
- Online: name (or `NAME.COM`) + a phone or URL fragment + state, no store number.
- Truncation is part of realism: names over the field limit are cut, not abbreviated neatly.
- Debit card on a checking account usually has a bank prefix (section 3); a credit card line is
  just the descriptor, no prefix, and the posting date is separate from the transaction date.

## 3. Checking account card rows (pick one house style)

| Style | Template | Example |
|---|---|---|
| A | `CHECKCARD {MMDD} NAME #{STORE} {CITY} {ST}` | `CHECKCARD 0312 KROGER #{STORE} COLUMBUS OH` |
| B | `POS PURCHASE NAME #{STORE} {CITY} {ST}` | `POS PURCHASE SHELL OIL {STORE} DALLAS TX` |
| C | `DEBIT CARD PURCHASE NAME {CITY} {ST}` | `DEBIT CARD PURCHASE LUNA TACOS AUSTIN TX` |

- Style A carries the date because the bank echoes the swipe date; use `{MMDD}` there.
- Prefix eats 11-20 chars. Shorten big names rather than lose `{ST}`.
- Online debit: `CHECKCARD {MMDD} NETFLIX.COM LOS GATOS CA` is the classic shape. Use `NAME.COM`, a toll-free
  phone fragment or a URL fragment for online merchants, never a store number.
- PIN debit (grocery cashback, gas) can read `PIN PURCHASE ...` or `POS DEBIT ...`; rare, keep < 10%.
- Recurring card subscriptions: `RECURRING PAYMENT` is sometimes appended: `NETFLIX.COM CA RECURRING`.
- Refund: type REFUND, credit, same descriptor as the purchase, often with `RETURN` or `CREDIT` in
  front: `PURCHASE RETURN TARGET #{STORE}`; it posts 1-5 days after the return and equals the
  original amount (or a partial).

## 4. Credit card statement rows

- No prefix, no `CHECKCARD`. Line = descriptor only, optional `{CITY} {ST}`.
- Examples: `TRADER JOE S #{STORE} {CITY} {ST}`, `SHELL OIL {STORE} {CITY} {ST}`,
  `TST* LUNA TACOS {CITY} {ST}`, `AMZN MKTP US*{REF} AMZN.COM/BILL WA`, `NETFLIX.COM LOS GATOS CA`.
- Transaction date and posted date differ by 0-3 days (weekend/holiday purchases post the next
  business day). Gas pumps and restaurants may post a day later than the swipe.
- Credit side of a payment, interest and fee rows: sections 10 to 12.

## 5. Processor prefixes (the single most recognisable realism signal)

| Prefix | Meaning | Format and examples |
|---|---|---|
| `SQ *` | Square seller | `SQ *BLUE DOOR COFFEE {CITY} {ST}`; space before `*`, none after; name up to ~20 chars |
| `TST*` | Toast restaurant POS | `TST* LUNA TACOS {CITY} {ST}`; asterisk, then a space, then the restaurant name |
| `PAYPAL *` | PayPal checkout | `PAYPAL *PRINTSHOP4U 402-935-7733 CA`: seller's PayPal business name (often unlike the site name), then phone, then state |
| `SP ` | Shopify store | `SP PAPER GOODS CO` or `SP * PAPERGOODS`; small online brands |
| `GOOGLE *` | Google services | `GOOGLE *YOUTUBE PREMIUM` , `GOOGLE *STORAGE`, `GOOGLE *WORKSPACE` |
| `APPLE.COM/BILL` | Apple subscriptions | `APPLE.COM/BILL ITUNES.COM CA`; amounts like 0.99, 2.99, 9.99, 14.99 |
| `AMZN Mktp US*` | Amazon marketplace order | `AMZN MKTP US*{REF} AMZN.COM/BILL WA`; `*` then ~9-10 alnum order code |
| `AMAZON PRIME*` | Prime membership | `AMAZON PRIME*{REF} AMZN.COM/BILL WA`; 14.99 monthly or 139.00 yearly |
| `AMAZON.COM*` | Amazon retail (older/other) | `AMAZON.COM*{REF} AMZN.COM/BILL WA` |
| `UBER *TRIP` / `UBER *EATS` | Uber | `UBER *TRIP HELP.UBER.COM CA`, `UBER *EATS HELP.UBER.COM CA` |
| `LYFT *RIDE` | Lyft | `LYFT *RIDE THU 6PM LYFT.COM CA` (day/time fragment is typical) |
| `DD *DOORDASH` | DoorDash order | `DD *DOORDASH LUNATACOS DOORDASH.COM CA` |
| `CASH APP*` | Cash App spend | `CASH APP*JANE DOE` rare, individuals only |

- Amazon: marketplace orders are `AMZN MKTP US*` plus a random code. Amazon's own retail and
  Prime use `AMAZON.COM*` / `AMAZON PRIME*`. Order amounts are odd-cent and include tax.
  Several Amazon orders in one month with different codes are normal; identical amount AND code
  twice is not.
- Toast and Square lines show the restaurant's/shop's trade name, never "TOAST" or "SQUARE" alone.
  The purchaser sees the merchant; the payee only sees "SQUARE INC" on payouts (section 7).
- Processor prefixes eat characters: `TST* ` is 5, `SQ *` is 4. Truncate the name, not the state.

## 6. Merchant name and number patterns by category

- Grocery: `TRADER JOE S #{STORE}`, `KROGER #{STORE}`, `ALDI {STORE}`, `PUBLIX #{STORE}`,
  `WAL-MART #{STORE}` / `WM SUPERCENTER #{STORE}`, `COSTCO WHSE #{STORE}`, `H-E-B #{STORE}`.
- Big box / pharmacy: `TARGET {STORE}` (real descriptors pad with spaces; the engine collapses them),
  `CVS/PHARMACY #{STORE}`, `WALGREENS #{STORE}`, `HOME DEPOT #{STORE}`, `LOWES #{STORE}`.
- Fuel: brand + site number, city optional: `SHELL OIL {STORE}`, `EXXONMOBIL {STORE}`,
  `CHEVRON {STORE}`, `QT {STORE}` (QuikTrip), `WAWA {STORE}`, `SHEETZ {STORE}`, `SPEEDWAY {STORE}`.
- Fast food: `MCDONALD S F{STORE}`, `CHIPOTLE {STORE}`, `STARBUCKS STORE {STORE}`, `DUNKIN #{STORE}`,
  `TACO BELL #{STORE}`, `CHICK-FIL-A #{STORE}`. Franchises sometimes use the franchisee: `DUNKIN #{STORE} Q35`.
- Independent restaurant / cafe: name + city, or via `TST*` / `SQ *`. No store number.
- Utilities (ACH): `DUKE ENERGY PAYMENT`, `CITY OF {CITY} UTILITIES`, `COMCAST CABLE COMM WEB PMTS`.
- Telecom: `VERIZON WIRELESS PAYMENTS`, `T-MOBILE AUTOPAY`, `AT&T PAYMENT`.
- Insurance: `GEICO *AUTO`, `STATE FARM RO {ID} INS`, `PROGRESSIVE INS PREM` (debit, monthly or semiannual).

## 7. ACH credits: income deposits

ACH fields: Company Name (16 chars max), Company Entry Description (10 chars max: `PAYROLL`,
`PAYMENT`, `TRANSFER`, `WEB PMTS`, `DIRECT DEP`, `VENDOR PMT`, `TAX PYMT`), plus an id. A bank
shows `COMPANY NAME ENTRY DESC [ID]`. Company names longer than 16 are cut mid-word (`TOYOTA MOTOR CRE`). Payer strings below are
plausible conventions: when researched merchant facts give the real wording, follow the facts.

| Channel | Template | Notes |
|---|---|---|
| `stripe` | `STRIPE TRANSFER ST-{ID}` | payout net of fees; 2 business days after charge; amount odd cents |
| `square` | `SQUARE INC TRANSFER` | next-business-day transfer; instant/same-day 1.5-1.95% fee is netted |
| `paypal` | `PAYPAL TRANSFER` (never repeat PAYPAL after it) | withdrawal of a balance; instant withdrawals show `PAYPAL INST XFER` |
| `venmo` | `VENMO CASHOUT` | free standard cashout 1-3 business days; instant fee 1.75% (min 25c, max $25) |
| `platform_payout` | `UBER USA PAYOUT`, `DOORDASH INC PAYMENT` | weekly; company name <= 16 chars; generic entry description |
| `ach_client` | `ACME CONSULTING PAYMENT` (payer name first, as its ACH company name) | round-ish invoice totals; irregular days |
| `zelle` | `ZELLE PAYMENT FROM JANE DOE {REF}` | see section 9 |
| `mobile_deposit` | `MOBILE DEPOSIT` / `CHECK DEPOSIT` | whole-dollar check amount; first $275 available next day |
| `card_sales` | `TOAST INC DEPOSIT` / `SQUARE INC DEPOSIT` (processor name first) | business accounts; section 7a |

7a. Business card-sales deposits are daily batches, net of 2.6%-3.5% fees, posted on business
days only; weekend sales land Monday or Tuesday. Toast and Square batches read like `TOAST INC DEPOSIT` / `SQUARE INC DEPOSIT`.

- Payroll credits exist only for W-2 employees. Personal personas in this game are self-employed:
  never write `PAYROLL`, `DIRECT DEP` or `SALARY` for them.
- Never put the platform's brand and a customer's name on the same line unless it is Zelle/Venmo.

## 8. ACH debits: bills, payroll, taxes

- Bill pay (utilities, phone, insurance, loans): `BILLER NAME(16) WEB PMTS`, `NAME AUTOPAY`,
  `NAME PAYMENT ID {ID}` or no entry detail at all. Examples: `COMCAST CABLE COMM WEB PMTS`,
  `DUKE ENERGY BILL PAY`, `TOYOTA MOTOR CRE AUTO PYMT` (name cut at 16), `NAVIENT STDNT LN PMT`.
  Never a random letter code after it: the same bill reads identically every month.
- Rent: `APPFOLIO RENT {ID}`, `YARDI PAYMENT {ID}`, `NAME RENT`; or type CHECK
  (`CHECK 1042`); or Zelle to landlord. Rent never carries a city/state.
- Payroll (businesses): Gusto debits are separate lines by purpose: `GUSTO NET {ID}`,
  `GUSTO TAX {ID}`, `GUSTO FEE {ID}`. ADP: `ADP WAGE PAY`, `ADP TX/FINCL SVC`, `ADP PAYROLL FEES`.
  Debit usually 1-4 business days before payday. Taxes and net pay are separate debits.
- IRS estimated tax (Direct Pay): `IRS USATAXPYMT {REF}` (company IRS, entry description
  USATAXPYMT; the engine writes the 15-digit numeric trace that starts 2706). Due Apr 15, Jun 15, Sep 15, Jan 15; weekend/holiday shifts to next business day.
- IRS refund credit: `IRS TREAS 310 TAX REF`; installment/other: `IRS USATAXPYMT`.
- State tax: `STATE OF OH DEPT TAX PYMT`, `CA FTB TAX PYMT`, `TX COMPTROLLER SALES TAX`. Only states
  that levy the tax; sales tax remittance is a business debit (monthly/quarterly).
- Insurance premium: `STATE FARM RO {ID} INS PREM`, `PROGRESSIVE INS PREM`, six-month or monthly, same day each time.
- Wire (rare): `WIRE TRANSFER OUT`, fee separate. Do not use for ordinary persona bills.

## 9. Zelle, Venmo, Cash App, PayPal person-to-person

- Zelle type = `ZELLE`. Credit: `ZELLE PAYMENT FROM JANE DOE {REF}`. Debit: `ZELLE PAYMENT TO JOHN ROE {REF}`.
  `{REF}` plays the confirmation code (alphanumeric, ~11 chars on real statements).
- Zelle counterparty: a person's real-looking first and last name (landlord, client, friend) or a
  small business. Amounts are round: 20, 25, 40, 75, 120, 250, 1,200 (rent). A cent value is rare.
- Memo text optional and short: `RENT MARCH`, `HAIRCUT`, `DINNER`.
- Venmo credit (business profile payments): `VENMO PAYMENT {REF}`; cashout to bank: `VENMO CASHOUT`.
  Funding a Venmo payment from checking: debit `VENMO PAYMENT {REF}`.
- PayPal: bank-side withdrawal `PAYPAL TRANSFER` credit; PayPal pulling funds for a purchase:
  debit `PAYPAL INST XFER {REF}`. Card-side purchases use `PAYPAL *NAME` (section 5).
- P2P counterparties are individuals; do not use a corporate name for Zelle from a "friend".

## 10. ATM

- Built-in key `atm`: `ATM WITHDRAWAL {MMDD} TERMINAL {REF}`; type ATM, debit.
- Variants: `NON-NETWORK ATM WITHDRAWAL {CITY} {ST}`, `ATM CASH WITHDRAWAL #{STORE} {CITY} {ST}`,
  `ATM DEPOSIT {CITY} {ST}` (cash/check deposit).
- Amounts: multiples of $20 ONLY ($20, $40, $60 ... $200; rarely above $400). No $10 steps, no odd amounts.
- Fees (key `fee-atm`): surcharge by the ATM owner averages about $3.22 and the account holder's
  own out-of-network fee about $1.64 (combined about $4.86 in 2025). In-network machines have no
  fee. Show fees as a second line right after the withdrawal, same date: `ATM FEE REF {REF}`.
- Foreign withdrawal fee: `FOREIGN ATM FEE` plus about 3% on the currency conversion.

## 11. Interest and fees (key `interest`, `fee-overdraft`)

- Credit interest: `PURCHASE INTEREST CHARGED` (built in), type INTEREST. Posts on the last day of
  the cycle (closing date). Appears only if the previous statement balance was not paid in full.
  Interest = average daily balance x APR / 365 x days in cycle (approx.). APR 20%-29.99%.
- Cash advance interest: `CASH ADVANCE INTEREST CHARGE` ; separate rate, usually higher.
- Savings interest: `INTEREST PAYMENT` or `INTEREST EARNED`, tiny (cents to a few dollars).
- Monthly maintenance fee: `MONTHLY SERVICE FEE` (commonly $5 to $15, waived by balance or
  deposits; same amount and day each month if present).
- Overdraft: `OVERDRAFT FEE`, always exactly $35.00 in this product (one per overdrawing item); returned item: `NSF FEE` /
  `RETURNED ITEM FEE`. Fees appear on the same day as the item that overdrew, one per item.
- Card fees: `ANNUAL MEMBERSHIP FEE` (95 or 0), `LATE FEE` (about $30-$41 on first / repeat),
  `FOREIGN TRANSACTION FEE` (about 3%, immediately after the foreign purchase).
- Business: `ANALYSIS SERVICE CHG`, `MERCHANT SVC FEE` (processor monthly), `WIRE FEE` ($15-$30).
- Never an interest line on a fully paid card, never a fee line on a day with no trigger.

## 12. Both sides of a credit card payment (key `card-payment`)

- Checking debit (type PAYMENT or TRANSFER): `ONLINE PMT TO CARD ENDING {LAST4}`. Alternatives:
  `AUTOPAY CREDIT CARD ****{LAST4}`, `PAYMENT TO CARD XXXX{LAST4}`.
- Card credit (direction credit): `ONLINE PAYMENT - THANK YOU` (built in). Alternatives:
  `AUTOPAY PAYMENT - THANK YOU`, `PAYMENT RECEIVED - THANK YOU`, `ACH PAYMENT THANK YOU`.
- The two rows have identical amounts, and the card row posts the same day or 1-2 business days
  later. The checking row never lands on a weekend/holiday; the card posting date follows it.
- `{LAST4}` on the checking row = the credit card's last four. The card row never names the
  checking account.
- Autopay = same day each month; manual pay = varying day around the due date. Full payment equals
  the statement balance; partial equals the minimum or round numbers ($100, $250, $500).

## 13. Memo field (the English translation)

- Short, plain, human: "Payment to Duke Energy", "Interest charged on purchases", "Zelle from
  Jane Doe", "Square deposit - card sales". Never repeat the cryptic code.

## 14. Red flags an auditor should catch

- Lowercase text, or a line over 60 characters, or `{PLACEHOLDER}` left unfilled.
- `CHECKCARD` on a credit card row, or two different checking prefixes on one account.
- `SQ *`/`TST*` on a national chain; no `TST*` on a family restaurant that obviously uses Toast.
- A Zelle row with a corporate name or cents; a Zelle memo that is a sentence.
- A store number on a sole proprietor, or a different store number for the same chain merchant.
- Amazon lines repeating the same code; AMAZON PRIME at a non-Prime price.
- `PAYROLL`, `DIRECT DEP`, `SALARY` on a self-employed persona; `IRS USATAXPYMT` outside April,
  June, September, January (unless a balance-due payment).
- An interest charge on a card paid in full, or a card payment whose two sides differ in amount.
- Rows on Sat/Sun/Fed holidays for ACH or card payment posting dates.
- A real bank brand, logo, routing number or full card number anywhere.

## 15. Per-bank line anatomy

One ledger feeds 11 layouts, and the same swipe must read differently in each. That per-bank
table (row shapes, which column each bank adds, where the card tail goes) lives in the
`bank-forms` skill under `references/<bank>.md` — it is injected only for the layout being
rendered, so do not restate house styles here.

What stays true here: the descriptor text itself is terse, uppercase and truncated, and the
engine (not the model) decides the final line. Do not write a bank-specific prefix into a
merchant template; the layout adapter adds it.

## Sources

- https://developer.paypal.com/braintree/articles/au/transactions/descriptors
- https://developer.cybersource.com/docs/cybs/en-us/api-fields/reference/all/so/api-fields/merch-info-merchant-descriptor-name.html
- https://docs.apis-fiserv.com/latam/docs/soft-descriptor
- https://bell.bank/-/media/project/bell/bank-redesign/healthcarebank/guides-and-tutorials/nacha-specifications.pdf
- https://www.thomsonreuters.com/en-us/help/accounting-cs-client-access/direct-deposit/ach-structure-and-contents.html
- https://support.toasttab.com/en/article/Understand-Toast-Charge-Codes-on-Bank-Statements
- https://squareup.com/help/us/en/article/5438
- https://community.squareup.com/t5/Payments-Troubleshooting/bank-statement-name-adjustment/m-p/762297
- https://docs.stripe.com/payouts/statement-descriptors
- https://support.gusto.com/article/186733245100000/processing-payroll-overview-for-admins
- https://www.irs.gov/payments/direct-pay-help
- https://bank-charge-explainer.vercel.app/descriptor/amzn-mktp-us
- https://www.paypal-community.com/t5/About-Payments/What-is-quot-Withdrawal-PAYPAL-INST-XFER-quot/m-p/1162642
- https://yourbankstatementconverter.com/blog/what-does-venmo-show-up-as-on-bank-statement/
- https://www.bankrate.com/banking/checking/checking-account-survey/
- https://www.bankrate.com/press-releases/atm-fees-hit-record-high-for-third-consecutive-year/
- https://www.nerdwallet.com/blog/credit-cards/how-credit-card-interest-calculated
