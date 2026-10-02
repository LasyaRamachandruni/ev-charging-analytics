import { useMemo, useState } from "react";
import type { Station } from "../api/client";
import { fmtInt, fmtKwh, fmtPct } from "../lib/format";

type SortKey = "occupied" | "idle" | "energy";
const SORTS: Record<SortKey, { label: string; value: (s: Station) => number }> = {
  occupied: { label: "Most occupied", value: (s) => s.occupied_share },
  idle: { label: "Most time parked after charging", value: (s) => s.idle_share_of_occupied ?? 0 },
  energy: { label: "Most energy", value: (s) => s.total_kwh },
};
const PREVIEW = 12;

/**
 * Each bar is the share of the whole period a station had a car plugged in,
 * split into time actually charging and time a fully charged car sat there.
 */
export function Stations({ stations, onPick }: { stations: Station[]; onPick?: (id: string) => void }) {
  const [sort, setSort] = useState<SortKey>("occupied");
  const [all, setAll] = useState(false);
  const sorted = useMemo(
    () => [...stations].sort((a, b) => SORTS[sort].value(b) - SORTS[sort].value(a) || a.station_id.localeCompare(b.station_id)),
    [stations, sort],
  );
  const shown = all ? sorted : sorted.slice(0, PREVIEW);
  const scale = Math.max(...stations.map((s) => s.occupied_share), 0.01);

  return (
    <div className="stations">
      <div className="toolbar">
        <label>
          Sort by{" "}
          <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            {Object.entries(SORTS).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </select>
        </label>
        <p className="key">
          <span className="swatch charging" /> charging <span className="swatch idle" /> plugged in, already charged
        </p>
      </div>
      <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">Station</th>
            <th scope="col" className="num">
              Sessions
            </th>
            <th scope="col" className="num">
              Energy
            </th>
            <th scope="col" className="bar-col">
              Share of the period with a car plugged in
            </th>
            <th scope="col" className="num">
              Parked after charging
            </th>
          </tr>
        </thead>
        <tbody>
          {shown.map((s) => (
            <tr key={s.station_id}>
              <th scope="row">
                {onPick ? (
                  <button className="link" onClick={() => onPick(s.station_id)} title="Show this station's sessions">
                    {s.station_id}
                  </button>
                ) : (
                  s.station_id
                )}
              </th>
              <td className="num">{fmtInt(s.sessions)}</td>
              <td className="num">{fmtKwh(s.total_kwh)}</td>
              <td className="bar-col">
                <div className="bar-cell">
                  <div className="track">
                    <div className="split-bar" style={{ width: `${(s.occupied_share / scale) * 100}%` }}>
                      {/* as fractions of the bar: flex-grow values summing to less than 1 leave it partly empty */}
                      <span className="charging" style={{ width: `${(s.charging_share / s.occupied_share) * 100}%` }} />
                      <span className="idle" style={{ flexGrow: 1 }} />
                    </div>
                  </div>
                <span className="bar-value">{fmtPct(s.occupied_share)}</span>
                </div>
              </td>
              <td className="num">{fmtPct(s.idle_share_of_occupied)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {stations.length > PREVIEW && (
        <button className="quiet" onClick={() => setAll(!all)}>
          {all ? `Show top ${PREVIEW}` : `Show all ${stations.length} stations`}
        </button>
      )}
    </div>
  );
}
