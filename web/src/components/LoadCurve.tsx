import { useState } from "react";
import type { LoadProfile } from "../api/client";
import { fmtKw } from "../lib/format";
import { linear, niceMax } from "../lib/scale";
import { useWidth } from "../lib/useWidth";

const H = 300;
const M = { top: 16, right: 16, bottom: 30, left: 60 };

function clock(slot: number): string {
  const h = Math.floor(slot / 4);
  const m = (slot % 4) * 15;
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}${m ? `:${String(m).padStart(2, "0")}` : ""} ${h < 12 ? "am" : "pm"}`;
}

/** Average site power through the day: the shape every capacity and demand-charge decision starts from. */
export function LoadCurve({ profile }: { profile: LoadProfile }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const slots = profile.slots;
  const peakSlot = slots.reduce((a, b) => (b.weekday_avg_kw > a.weekday_avg_kw ? b : a), slots[0]);
  const top = Math.max(...slots.map((s) => Math.max(s.weekday_avg_kw, s.weekend_avg_kw)));
  const { max, step } = niceMax(top * 1.2); // headroom for the peak label

  const x = linear(0, 96, M.left, width - M.right);
  const y = linear(0, max, H - M.bottom, M.top);
  const line = (key: "weekday_avg_kw" | "weekend_avg_kw") =>
    slots.map((s, i) => `${i ? "L" : "M"}${x(s.slot + 0.5).toFixed(1)},${y(s[key]).toFixed(1)}`).join("");
  const area = `${line("weekday_avg_kw")}L${x(95.5)},${y(0)}L${x(0.5)},${y(0)}Z`;
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step);
  const hovered = hover === null ? null : slots[hover];

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const slot = Math.floor(((e.clientX - box.left) / box.width) * 96);
    setHover(Math.min(95, Math.max(0, slot)));
  };

  const peakX = x(peakSlot.slot + 0.5);
  const labelLeft = peakX > width * 0.6;
  const weekendPeak = slots.reduce((a, b) => (b.weekend_avg_kw > a.weekend_avg_kw ? b : a), slots[0]);

  return (
    <div ref={ref} className="load-curve">
      <svg
        width={width}
        height={H}
        role="img"
        aria-label={`Weekday average power peaks at ${fmtKw(peakSlot.weekday_avg_kw)} around ${clock(peakSlot.slot)}.`}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} />
            <text className="axis" x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end">
              {t === max ? `${t} kW` : `${t}`}
            </text>
          </g>
        ))}
        {(width < 640 ? [0, 24, 48, 72, 96] : [0, 12, 24, 36, 48, 60, 72, 84, 96]).map((s) => (
          <text key={s} className="axis" x={x(s)} y={H - 8} textAnchor={s === 0 ? "start" : s === 96 ? "end" : "middle"}>
            {s === 96 ? "midnight" : clock(s)}
          </text>
        ))}

        <path className="area-weekday" d={area} />
        <path className="line-weekend" d={line("weekend_avg_kw")} />
        <path className="line-weekday" d={line("weekday_avg_kw")} />

        <text className="series-label weekend" x={x(weekendPeak.slot + 0.5)} y={y(weekendPeak.weekend_avg_kw) - 10} textAnchor="middle">
          Weekends
        </text>

        <circle className="peak-dot" cx={peakX} cy={y(peakSlot.weekday_avg_kw)} r={4.5} />
        {/* label sits above the peak, so the curve (which only falls away from it) never crosses the text */}
        <text
          className="peak-label"
          x={peakX + (labelLeft ? -10 : 10)}
          y={y(peakSlot.weekday_avg_kw) - 26}
          textAnchor={labelLeft ? "end" : "start"}
        >
          <tspan className="series-label weekday">Weekdays</tspan>
          <tspan x={peakX + (labelLeft ? -10 : 10)} dy="1.25em">
            average peak {fmtKw(peakSlot.weekday_avg_kw)} at {clock(peakSlot.slot)}
          </tspan>
        </text>

        {hovered && (
          <g className="crosshair" pointerEvents="none">
            <line x1={x(hovered.slot + 0.5)} x2={x(hovered.slot + 0.5)} y1={M.top} y2={H - M.bottom} />
            <circle className="dot-weekday" cx={x(hovered.slot + 0.5)} cy={y(hovered.weekday_avg_kw)} r={3.5} />
            <circle className="dot-weekend" cx={x(hovered.slot + 0.5)} cy={y(hovered.weekend_avg_kw)} r={3.5} />
          </g>
        )}
        <rect
          className="hit"
          x={M.left}
          y={M.top}
          width={Math.max(0, width - M.left - M.right)}
          height={H - M.top - M.bottom}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
      <p className="readout" aria-live="polite">
        {hovered ? (
          <>
            {clock(hovered.slot)}: <span className="weekday">{fmtKw(hovered.weekday_avg_kw)}</span> on an average weekday,{" "}
            <span className="weekend">{fmtKw(hovered.weekend_avg_kw)}</span> on a weekend day. Highest single weekday reading{" "}
            {fmtKw(hovered.weekday_max_kw)}.
          </>
        ) : (
          <>
            Averaged over {profile.weekdays} weekdays and {profile.weekend_days} weekend days. Point at the chart to read a time.
          </>
        )}
      </p>
    </div>
  );
}
