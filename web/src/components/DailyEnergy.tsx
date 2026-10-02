import type { DailyRow } from "../api/client";
import { fmtDate, fmtInt, fmtKwh } from "../lib/format";
import { linear, niceMax } from "../lib/scale";
import { useWidth } from "../lib/useWidth";

const H = 180;
const M = { top: 10, right: 8, bottom: 24, left: 64 };

export function DailyEnergy({ rows }: { rows: DailyRow[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  if (!rows.length) return <p className="empty">No charging in this date range. Widen the range to see daily energy.</p>;
  const { max, step } = niceMax(Math.max(...rows.map((r) => r.energy_kwh)));
  const band = (width - M.left - M.right) / rows.length;
  const y = linear(0, max, H - M.bottom, M.top);
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step);
  const labelEvery = Math.ceil(rows.length / Math.max(1, Math.floor((width - M.left) / 70)));

  return (
    <div ref={ref}>
      <svg width={width} height={H} role="img" aria-label={`Energy delivered per day, ${rows.length} days.`}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} />
            <text className="axis" x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end">
              {t === max ? `${fmtInt(t)} kWh` : fmtInt(t)}
            </text>
          </g>
        ))}
        {rows.map((r, i) => (
          <rect
            key={r.local_date}
            className={r.is_weekend ? "bar weekend" : "bar weekday"}
            x={M.left + i * band + band * 0.15}
            width={Math.max(1, band * 0.7)}
            y={y(r.energy_kwh)}
            height={y(0) - y(r.energy_kwh)}
          >
            <title>{`${fmtDate(r.local_date)}: ${fmtKwh(r.energy_kwh)}, ${r.sessions} sessions`}</title>
          </rect>
        ))}
        {rows.map((r, i) =>
          i % labelEvery === 0 ? (
            <text key={r.local_date} className="axis" x={M.left + (i + 0.5) * band} y={H - 6} textAnchor="middle">
              {fmtDate(r.local_date, false)}
            </text>
          ) : null,
        )}
      </svg>
    </div>
  );
}
