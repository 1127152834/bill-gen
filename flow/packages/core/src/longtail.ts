/**
 * The long tail: merchants a real household touches that no research pass would ever surface.
 *
 * Researched facts are dozen-deep (one city, one model call), so a statement built only from them
 * repeats the same eight names for three months. Real statements are the opposite: a handful of
 * anchors, then a smear of one-off names — a vending machine, a parking meter, the pet store, the
 * coffee shop two blocks further away. This table is that smear: static, place-independent, and
 * assembled with the request's city so the rows still read local.
 *
 * Rows are grouped by `role`:
 * - `discretionary` — repeatable errands. The scheduler may place these any cycle.
 * - `misc` — sub-$15 noise (vending, toll, app store, tip adjustment). Injected after the schedule.
 * - `event` — one-off shocks (urgent care, a brake job, a flight, a ticket). Injected at most once
 *   per period, and they move the balance curve.
 *
 * `account` is `both` when the same purchase can land on a card or run as a debit-card POS on
 * checking — real people do both, which is why checking statements are not all ACH.
 */
export type LongTailRole = 'discretionary' | 'misc' | 'event';

export type LongTailMerchant = {
  /** Stable merchantKey; also the key `resolveTemplate` looks up. */
  key: string;
  /** Descriptor template. `{STORE}` becomes a per-merchant store number, `{CITY}`/`{ST}` the place. */
  template: string;
  memo: string;
  /** Research-style category, for occupation-fit and for grouping in the UI. */
  category: string;
  account: 'checking' | 'credit' | 'both';
  role: LongTailRole;
  /** Ticket size in cents, inclusive. */
  ticket: { min: number; max: number };
};

/** `[key, template, memo, category, account, role, minCents, maxCents]` */
type Row = [string, string, string, string, LongTailMerchant['account'], LongTailRole, number, number];

const ROWS: readonly Row[] = [
  // ── coffee / quick service ────────────────────────────────────────────────────────────────
  ['lt-starbucks', 'STARBUCKS # {STORE} {CITY} {ST}', 'Card purchase', 'coffee', 'both', 'discretionary', 435, 2495],
  ['lt-dunkin', "DUNKIN' #{STORE} {CITY} {ST}", 'Card purchase', 'coffee', 'both', 'discretionary', 289, 1840],
  ['lt-blue-bottle', 'SQ *BLUE BOTTLE COFFEE #{STORE}', 'Card purchase', 'coffee', 'credit', 'discretionary', 480, 2650],
  ['lt-local-roaster', 'SQ *{CITY} COFFEE ROASTERS', 'Card purchase', 'coffee', 'both', 'discretionary', 395, 2280],
  ['lt-mcdonalds', "MCDONALD'S F{STORE} {CITY} {ST}", 'Card purchase', 'restaurant', 'both', 'discretionary', 349, 1899],
  ['lt-chickfila', 'CHICK-FIL-A #{STORE} {CITY} {ST}', 'Card purchase', 'restaurant', 'both', 'discretionary', 615, 2340],
  ['lt-tacobell', 'TACO BELL {STORE} {CITY} {ST}', 'Card purchase', 'restaurant', 'both', 'discretionary', 402, 1675],
  ['lt-chipotle', 'CHIPOTLE {STORE} {CITY} {ST}', 'Card purchase', 'restaurant', 'both', 'discretionary', 895, 2180],
  ['lt-panera', 'PANERA BREAD #{STORE} {CITY} {ST}', 'Card purchase', 'restaurant', 'both', 'discretionary', 712, 2460],
  ['lt-subway', 'SUBWAY {STORE} {CITY} {ST}', 'Card purchase', 'restaurant', 'both', 'discretionary', 545, 1540],
  ['lt-wendys', "WENDY'S {STORE} {CITY} {ST}", 'Card purchase', 'restaurant', 'both', 'discretionary', 489, 1830],
  ['lt-whataburger', 'WHATABURGER #{STORE} {CITY} {ST}', 'Card purchase', 'restaurant', 'both', 'discretionary', 612, 1975],
  ['lt-fiveguys', 'FIVE GUYS {STORE} {CITY} {ST}', 'Card purchase', 'restaurant', 'both', 'discretionary', 1180, 2890],
  ['lt-sweetgreen', 'SWEETGREEN {STORE} {CITY} {ST}', 'Card purchase', 'restaurant', 'credit', 'discretionary', 1240, 2210],
  ['lt-local-thai', 'TST* {CITY} THAI KITCHEN', 'Card purchase', 'restaurant', 'both', 'discretionary', 1450, 6420],
  ['lt-local-taqueria', 'TST* TAQUERIA {CITY}', 'Card purchase', 'restaurant', 'both', 'discretionary', 780, 3450],
  ['lt-local-diner', 'SQ *{CITY} DINER', 'Card purchase', 'restaurant', 'both', 'discretionary', 940, 4180],
  // ── convenience / fuel ───────────────────────────────────────────────────────────────────
  ['lt-7eleven', '7-ELEVEN {STORE} {CITY} {ST}', 'Card purchase', 'convenience', 'both', 'discretionary', 215, 2340],
  ['lt-circle-k', 'CIRCLE K #{STORE} {CITY} {ST}', 'Card purchase', 'convenience', 'both', 'discretionary', 198, 2760],
  ['lt-qt', 'QT {STORE} {CITY} {ST}', 'Card purchase', 'fuel', 'both', 'discretionary', 1804, 6820],
  ['lt-sheetz', 'SHEETZ {STORE} {CITY} {ST}', 'Card purchase', 'fuel', 'both', 'discretionary', 1620, 7140],
  ['lt-wawa', 'WAWA {STORE} {CITY} {ST}', 'Card purchase', 'convenience', 'both', 'discretionary', 265, 3120],
  ['lt-shell', 'SHELL OIL {STORE} {CITY} {ST}', 'Card purchase', 'fuel', 'credit', 'discretionary', 2210, 8240],
  ['lt-chevron', 'CHEVRON {STORE} {CITY} {ST}', 'Card purchase', 'fuel', 'credit', 'discretionary', 2040, 7915],
  ['lt-exxon', 'EXXONMOBIL {STORE} {CITY} {ST}', 'Card purchase', 'fuel', 'credit', 'discretionary', 1985, 7480],
  ['lt-valero', 'VALERO {STORE} {CITY} {ST}', 'Card purchase', 'fuel', 'credit', 'discretionary', 1890, 7055],
  ['lt-buccees', "BUC-EE'S #{STORE} {CITY} {ST}", 'Card purchase', 'convenience', 'both', 'discretionary', 420, 5680],
  // ── grocery ─────────────────────────────────────────────────────────────────────────────
  ['lt-wholefoods', 'WHOLEFDS {CITY} #{STORE}', 'Card purchase', 'grocery', 'both', 'discretionary', 1840, 14850],
  ['lt-traderjoes', "TRADER JOE'S #{STORE} {CITY} {ST}", 'Card purchase', 'grocery', 'both', 'discretionary', 1420, 11280],
  ['lt-kroger', 'KROGER #{STORE} {CITY} {ST}', 'Card purchase', 'grocery', 'both', 'discretionary', 1150, 16740],
  ['lt-safeway', 'SAFEWAY #{STORE} {CITY} {ST}', 'Card purchase', 'grocery', 'both', 'discretionary', 1080, 15220],
  ['lt-aldis', 'ALDI {STORE} {CITY} {ST}', 'Card purchase', 'grocery', 'both', 'discretionary', 890, 9840],
  ['lt-sprouts', 'SPROUTS FARMERS MKT {STORE}', 'Card purchase', 'grocery', 'both', 'discretionary', 1260, 10640],
  ['lt-heb', 'H-E-B #{STORE} {CITY} {ST}', 'Card purchase', 'grocery', 'both', 'discretionary', 975, 17160],
  ['lt-publix', 'PUBLIX #{STORE} {CITY} {ST}', 'Card purchase', 'grocery', 'both', 'discretionary', 1180, 14390],
  ['lt-costco', 'COSTCO WHSE #{STORE} {CITY} {ST}', 'Card purchase', 'grocery', 'both', 'discretionary', 4820, 27450],
  ['lt-target', 'TARGET {STORE} {CITY} {ST}', 'Card purchase', 'online retail', 'both', 'discretionary', 1420, 12860],
  ['lt-walmart', 'WALMART SUPERCENTER {STORE}', 'Card purchase', 'grocery', 'both', 'discretionary', 1180, 13940],
  ['lt-farmers-market', 'SQ *{CITY} FARMERS MARKET', 'Card purchase', 'grocery', 'both', 'discretionary', 620, 5480],
  // ── pharmacy / health ───────────────────────────────────────────────────────────────────
  ['lt-cvs', 'CVS/PHARMACY #{STORE} {CITY} {ST}', 'Card purchase', 'pharmacy', 'both', 'discretionary', 640, 8640],
  ['lt-walgreens', 'WALGREENS #{STORE} {CITY} {ST}', 'Card purchase', 'pharmacy', 'both', 'discretionary', 520, 7310],
  ['lt-riteaid', 'RITE AID #{STORE} {CITY} {ST}', 'Card purchase', 'pharmacy', 'both', 'discretionary', 480, 6220],
  ['lt-quest', 'QUEST DIAGNOSTICS {STORE}', 'Lab work', 'pharmacy', 'credit', 'discretionary', 2200, 18400],
  ['lt-urgentcare', 'TST* {CITY} URGENT CARE', 'Copay', 'pharmacy', 'credit', 'event', 7500, 32500],
  ['lt-dental', 'TST* {CITY} FAMILY DENTAL', 'Dental work', 'pharmacy', 'checking', 'event', 9500, 68000],
  // ── online / marketplace ────────────────────────────────────────────────────────────────
  ['lt-amzn-mktp', 'AMZN Mktp US*{REF}', 'Card purchase', 'online retail', 'both', 'discretionary', 620, 9860],
  ['lt-amazon-com', 'AMAZON.COM*{REF}', 'Card purchase', 'online retail', 'both', 'discretionary', 540, 12480],
  ['lt-ebay', 'EBAY O*{REF}', 'Card purchase', 'online retail', 'credit', 'discretionary', 830, 14200],
  ['lt-etsy', 'ETSY.COM {STORE}', 'Card purchase', 'online retail', 'credit', 'discretionary', 740, 8860],
  ['lt-temu', 'TEMU.COM {REF}', 'Card purchase', 'online retail', 'credit', 'discretionary', 310, 6240],
  ['lt-shein', 'SHEIN.COM {REF}', 'Card purchase', 'online retail', 'credit', 'discretionary', 480, 7180],
  ['lt-insta', 'INSTACART*{CITY} {REF}', 'Card purchase', 'grocery', 'credit', 'discretionary', 2840, 16420],
  ['lt-doordash', 'DD *DOORDASH {CITY}', 'Card purchase', 'restaurant', 'credit', 'discretionary', 1580, 7420],
  ['lt-ubereats', 'UBER *EATS', 'Card purchase', 'restaurant', 'credit', 'discretionary', 1640, 6890],
  ['lt-grubhub', 'GRUBHUB*{CITY} {REF}', 'Card purchase', 'restaurant', 'credit', 'discretionary', 1720, 7150],
  // ── subscriptions / digital ─────────────────────────────────────────────────────────────
  ['lt-netflix', 'NETFLIX.COM', 'Membership', 'subscription', 'credit', 'discretionary', 1599, 2299],
  ['lt-spotify', 'SPOTIFY USA', 'Membership', 'subscription', 'credit', 'discretionary', 1199, 1699],
  ['lt-hulu', 'HULU {REF}', 'Membership', 'subscription', 'credit', 'discretionary', 799, 1799],
  ['lt-disney', 'DISNEY PLUS', 'Membership', 'subscription', 'credit', 'discretionary', 999, 1999],
  ['lt-youtube', 'GOOGLE *YOUTUBEPREMIUM', 'Membership', 'subscription', 'credit', 'discretionary', 1399, 2299],
  ['lt-icloud', 'APPLE.COM/BILL', 'Storage and services', 'subscription', 'credit', 'discretionary', 99, 2999],
  ['lt-playstore', 'GOOGLE *PLAY', 'App purchase', 'subscription', 'credit', 'discretionary', 99, 1499],
  ['lt-openai', 'OPENAI *CHATGPT SUBSCR', 'Membership', 'subscription', 'credit', 'discretionary', 2000, 2000],
  ['lt-github', 'GITHUB INC', 'Membership', 'subscription', 'credit', 'discretionary', 400, 2100],
  ['lt-notion', 'NOTION LABS INC', 'Membership', 'subscription', 'credit', 'discretionary', 800, 1800],
  ['lt-dropbox', 'DROPBOX*{REF}', 'Membership', 'subscription', 'credit', 'discretionary', 999, 1999],
  ['lt-audible', 'AUDIBLE*{REF}', 'Membership', 'subscription', 'credit', 'discretionary', 1495, 2295],
  // ── transport / parking / toll ──────────────────────────────────────────────────────────
  ['lt-uber', 'UBER *TRIP HELP.UBER.COM', 'Ride', 'convenience', 'credit', 'discretionary', 985, 4620],
  ['lt-lyft', 'LYFT *RIDE {MMDD}', 'Ride', 'convenience', 'credit', 'discretionary', 1120, 5140],
  ['lt-metro', 'METRO TRANSIT {CITY}', 'Transit fare', 'convenience', 'both', 'discretionary', 275, 950],
  ['lt-parkmobile', 'PARKMOBILE {CITY}', 'Parking', 'convenience', 'both', 'misc', 200, 1180],
  ['lt-spothero', 'SPOTHERO*{CITY} PARKING', 'Parking', 'convenience', 'credit', 'misc', 300, 1650],
  ['lt-ezpass', 'EZPASS {ST} TOLL', 'Toll', 'convenience', 'checking', 'misc', 175, 1420],
  ['lt-tolltag', 'NORTHTEX TOLL {MMDD}', 'Toll', 'convenience', 'checking', 'misc', 160, 980],
  ['lt-vending', 'VENDING {CITY} {ST}', 'Vending', 'convenience', 'both', 'misc', 125, 375],
  ['lt-brake', 'TST* {CITY} AUTO REPAIR', 'Brake service', 'convenience', 'checking', 'event', 18500, 96000],
  ['lt-tires', 'DISCOUNT TIRE {STORE}', 'Tires', 'convenience', 'credit', 'event', 24000, 78000],
  ['lt-registration', 'COUNTY TAX OFFICE {ST}', 'Vehicle registration', 'tax', 'checking', 'event', 4800, 18600],
  // ── home / hardware / hobby ─────────────────────────────────────────────────────────────
  ['lt-homedepot', 'THE HOME DEPOT #{STORE}', 'Card purchase', 'online retail', 'both', 'discretionary', 1180, 23400],
  ['lt-lowes', "LOWE'S #{STORE} {CITY} {ST}", 'Card purchase', 'online retail', 'both', 'discretionary', 940, 19600],
  ['lt-ace', 'ACE HARDWARE {STORE}', 'Card purchase', 'online retail', 'both', 'discretionary', 480, 6420],
  ['lt-ikea', 'IKEA {STORE} {CITY} {ST}', 'Card purchase', 'online retail', 'credit', 'discretionary', 2480, 28600],
  ['lt-michaels', "MICHAEL'S #{STORE} {CITY} {ST}", 'Art supplies', 'art supplies', 'credit', 'discretionary', 620, 9240],
  ['lt-joann', 'JOANN # {STORE}', 'Craft supplies', 'art supplies', 'credit', 'discretionary', 540, 7180],
  ['lt-guitarcenter', 'GUITAR CENTER {STORE}', 'Music gear', 'art supplies', 'credit', 'event', 4800, 68000],
  ['lt-rei', 'REI #{STORE} {CITY} {ST}', 'Outdoor gear', 'online retail', 'credit', 'discretionary', 2240, 18400],
  ['lt-dicks', "DICK'S SPORTING GOODS {STORE}", 'Sporting goods', 'online retail', 'credit', 'discretionary', 1480, 12600],
  ['lt-basspro', 'BASS PRO SHOPS #{STORE}', 'Sporting goods', 'online retail', 'credit', 'discretionary', 1240, 16800],
  ['lt-petsmart', 'PETSMART #{STORE} {CITY} {ST}', 'Pet supplies', 'online retail', 'both', 'discretionary', 880, 9840],
  ['lt-petco', 'PETCO {STORE} {CITY} {ST}', 'Pet supplies', 'online retail', 'both', 'discretionary', 760, 11200],
  ['lt-chewy', 'CHEWY.COM {REF}', 'Pet supplies', 'online retail', 'credit', 'discretionary', 3420, 12480],
  ['lt-planetfit', 'PLANET FIT {STORE} {CITY} {ST}', 'Membership', 'subscription', 'checking', 'discretionary', 1000, 2499],
  // ── apparel / general retail ────────────────────────────────────────────────────────────
  ['lt-oldnavy', 'OLD NAVY {STORE} {CITY} {ST}', 'Card purchase', 'online retail', 'credit', 'discretionary', 1180, 14200],
  ['lt-hm', 'H&M {STORE} {CITY} {ST}', 'Card purchase', 'online retail', 'credit', 'discretionary', 940, 11600],
  ['lt-uniqlo', 'UNIQLO {STORE}', 'Card purchase', 'online retail', 'credit', 'discretionary', 1420, 12800],
  ['lt-tjmaxx', 'TJMAXX #{STORE} {CITY} {ST}', 'Card purchase', 'online retail', 'credit', 'discretionary', 860, 9840],
  ['lt-marshalls', 'MARSHALLS {STORE} {CITY} {ST}', 'Card purchase', 'online retail', 'credit', 'discretionary', 780, 10600],
  ['lt-nordrack', 'NORDSTROM RACK {STORE}', 'Card purchase', 'online retail', 'credit', 'discretionary', 1140, 16800],
  ['lt-bestbuy', 'BEST BUY {STORE} {CITY} {ST}', 'Electronics', 'online retail', 'credit', 'discretionary', 2480, 34800],
  ['lt-applestore', 'APPLE STORE R{STORE}', 'Electronics', 'online retail', 'credit', 'event', 3900, 148000],
  // ── entertainment ───────────────────────────────────────────────────────────────────────
  ['lt-amc', 'AMC {CITY} {STORE}', 'Movie tickets', 'restaurant', 'credit', 'discretionary', 1240, 4680],
  ['lt-ticketmaster', 'TICKETMASTER {REF}', 'Event tickets', 'online retail', 'credit', 'event', 4200, 42800],
  ['lt-steam', 'STEAM GAMES {REF}', 'Game purchase', 'online retail', 'credit', 'discretionary', 599, 5980],
  ['lt-playstation', 'PLAYSTATION NETWORK', 'Game purchase', 'subscription', 'credit', 'discretionary', 999, 6999],
  ['lt-nintendo', 'NINTENDO {REF}', 'Game purchase', 'subscription', 'credit', 'discretionary', 799, 5999],
  ['lt-barnes', 'BARNES & NOBLE #{STORE}', 'Books', 'art supplies', 'both', 'discretionary', 890, 5480],
  ['lt-eventbrite', 'EVENTBRITE {REF}', 'Event ticket', 'online retail', 'credit', 'discretionary', 1500, 12400],
  ['lt-museum', 'TST* {CITY} MUSEUM', 'Admission', 'art supplies', 'credit', 'discretionary', 1200, 6400],
  // ── personal care / grooming ────────────────────────────────────────────────────────────
  ['lt-greatclips', 'GREAT CLIPS {STORE} {CITY} {ST}', 'Haircut', 'convenience', 'both', 'discretionary', 1800, 3600],
  ['lt-sportclips', 'SPORT CLIPS {STORE}', 'Haircut', 'convenience', 'both', 'discretionary', 1900, 3200],
  ['lt-nails', 'SQ *{CITY} NAILS & SPA', 'Personal care', 'convenience', 'credit', 'discretionary', 2800, 7800],
  ['lt-laundry', 'SQ *{CITY} LAUNDROMAT', 'Laundry', 'convenience', 'checking', 'misc', 400, 2400],
  // ── wine / spirits ──────────────────────────────────────────────────────────────────────
  ['lt-totalwine', 'TOTAL WINE #{STORE}', 'Card purchase', 'grocery', 'credit', 'discretionary', 1420, 9840],
  ['lt-specs', "SPEC'S WINES {STORE}", 'Card purchase', 'grocery', 'credit', 'discretionary', 1640, 11200],
  ['lt-bevmo', 'BEVMO! {STORE}', 'Card purchase', 'grocery', 'credit', 'discretionary', 1180, 8640],
  // ── travel ──────────────────────────────────────────────────────────────────────────────
  ['lt-delta', 'DELTA AIR LINES {REF}', 'Airfare', 'travel', 'credit', 'event', 12800, 128000],
  ['lt-southwest', 'SOUTHWEST AIR {REF}', 'Airfare', 'travel', 'credit', 'event', 9800, 96000],
  ['lt-united', 'UNITED AIRLINES {REF}', 'Airfare', 'travel', 'credit', 'event', 11200, 112000],
  ['lt-airbnb', 'AIRBNB *{REF}', 'Lodging', 'travel', 'credit', 'event', 8600, 96000],
  ['lt-marriott', 'MARRIOTT {CITY} {ST}', 'Lodging', 'travel', 'credit', 'event', 12400, 68000],
  ['lt-hertz', 'HERTZ RENTAL {STORE}', 'Car rental', 'travel', 'credit', 'event', 6400, 42000],
  ['lt-expedia', 'EXPEDIA {REF}', 'Travel booking', 'travel', 'credit', 'event', 12800, 98000],
  // ── donations / civic ───────────────────────────────────────────────────────────────────
  ['lt-redcross', 'AMERICAN RED CROSS', 'Donation', 'convenience', 'both', 'misc', 500, 5000],
  ['lt-foodbank', 'SQ *{CITY} FOOD BANK', 'Donation', 'convenience', 'both', 'misc', 500, 3500],
  ['lt-gofundme', 'GOFUNDME *{REF}', 'Donation', 'convenience', 'credit', 'misc', 1000, 7500],
  ['lt-parkingticket', 'CITY OF {CITY} PARKING VIOL', 'Parking citation', 'tax', 'checking', 'event', 3500, 12000],
];

export const LONG_TAIL: readonly LongTailMerchant[] = ROWS.map(
  ([key, template, memo, category, account, role, min, max]) => ({
    key,
    template,
    memo,
    category,
    account,
    role,
    ticket: { min, max },
  }),
);

const BY_KEY = new Map(LONG_TAIL.map((m) => [m.key, m]));

/** The merchant behind a `lt-…` row, or undefined for anything else. */
export function longTailMerchant(key: string): LongTailMerchant | undefined {
  return BY_KEY.get(key);
}

/** True when the key names a row from this table (used to keep `resolveTemplate` honest). */
export function isLongTail(key: string): boolean {
  return BY_KEY.has(key);
}

/** Every row of one `role` a given account may carry. */
export function longTailPool(role: LongTailRole, account: 'checking' | 'credit'): readonly LongTailMerchant[] {
  return LONG_TAIL.filter((m) => m.role === role && (m.account === account || m.account === 'both'));
}
