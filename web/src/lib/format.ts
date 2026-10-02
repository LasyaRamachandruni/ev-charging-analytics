const int = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const one = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 });

export const fmtInt = (n: number) => int.format(n);
export const fmtKwh = (n: number) => (n >= 10_000 ? `${one.format(n / 1000)} MWh` : `${int.format(n)} kWh`);
export const fmtKw = (n: number) => `${int.format(n)} kW`;
export const fmtPct = (x: number | null | undefined) => (x == null ? "–" : `${Math.round(x * 100)}%`);

/** Decimal hours as "6 h 45 min" / "45 min". */
export function fmtHours(h: number | null | undefined): string {
  if (h == null) return "–";
  const total = Math.round(h * 60);
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  if (hours === 0) return `${mins} min`;
  return mins === 0 ? `${hours} h` : `${hours} h ${mins} min`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** "2019-02-15" -> "Feb 15, 2019". Parsed by hand: `new Date("2019-02-15")` is UTC midnight and shifts a day west of UTC. */
export function fmtDate(iso: string, withYear = true): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return withYear ? `${MONTHS[m - 1]} ${d}, ${y}` : `${MONTHS[m - 1]} ${d}`;
}

/** Local wall-clock timestamp (no zone) -> "Fri Feb 15, 9:45 am". */
export function fmtLocalTime(iso: string): string {
  const [date, time] = iso.split("T");
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  const weekday = DAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  const h12 = hh % 12 === 0 ? 12 : hh % 12;
  return `${weekday} ${MONTHS[m - 1]} ${d}, ${h12}:${String(mm).padStart(2, "0")} ${hh < 12 ? "am" : "pm"}`;
}

export function fmtRange(start: string, end: string): string {
  const sameYear = start.slice(0, 4) === end.slice(0, 4);
  return `${fmtDate(start, !sameYear)} to ${fmtDate(end)}`;
}
