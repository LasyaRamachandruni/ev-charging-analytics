import { useEffect, useState } from "react";
import { api, type Range, type Session, type Station } from "../api/client";
import { fmtHours, fmtInt, fmtLocalTime } from "../lib/format";

const PAGE = 50;

interface Props {
  site: string;
  range: Range;
  stations: Station[];
  station: string;
  onStation: (id: string) => void;
  total?: number;
}

/** Session log, newest first. "Show more" follows the API's keyset cursor, so page 40 is as fast as page 1. */
export function Sessions({ site, range, stations, station, onStation, total }: Props) {
  const [items, setItems] = useState<Session[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setItems([]);
    setCursor(null);
    setError(null);
    setBusy(true);
    api
      .sessions(site, { ...range, station: station || undefined, limit: PAGE }, ctrl.signal)
      .then((page) => {
        setItems(page.items);
        setCursor(page.next_cursor);
      })
      .catch((e: Error) => !ctrl.signal.aborted && setError(e.message))
      .finally(() => !ctrl.signal.aborted && setBusy(false));
    return () => ctrl.abort();
  }, [site, range.start, range.end, station]); // eslint-disable-line react-hooks/exhaustive-deps

  const more = async () => {
    if (!cursor) return;
    setBusy(true);
    try {
      const page = await api.sessions(site, { ...range, station: station || undefined, limit: PAGE, cursor });
      setItems((prev) => [...prev, ...page.items]);
      setCursor(page.next_cursor);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const stationTotal = station ? undefined : total;

  return (
    <div className="sessions">
      <div className="toolbar">
        <label>
          Station{" "}
          <select value={station} onChange={(e) => onStation(e.target.value)}>
            <option value="">All stations</option>
            {[...stations]
              .sort((a, b) => a.station_id.localeCompare(b.station_id))
              .map((s) => (
                <option key={s.station_id} value={s.station_id}>
                  {s.station_id}
                </option>
              ))}
          </select>
        </label>
        <p className="count" aria-live="polite">
          {items.length > 0 &&
            (stationTotal !== undefined
              ? `Showing ${fmtInt(items.length)} of ${fmtInt(stationTotal)} sessions`
              : `Showing ${fmtInt(items.length)} sessions`)}
        </p>
      </div>
      {error && <p className="error">Couldn't load sessions: {error}</p>}
      {!error && !busy && items.length === 0 && (
        <p className="empty">No sessions in this date range{station ? " at this station" : ""}. Try a wider range.</p>
      )}
      {items.length > 0 && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Plugged in</th>
                <th scope="col">Station</th>
                <th scope="col" className="num">
                  Stayed
                </th>
                <th scope="col" className="num">
                  Charging
                </th>
                <th scope="col" className="num">
                  Energy
                </th>
                <th scope="col">Request</th>
              </tr>
            </thead>
            <tbody>
              {items.map((s) => (
                <tr key={s.session_id}>
                  <td>{fmtLocalTime(s.connected_at_local)}</td>
                  <td>{s.station_id}</td>
                  <td className="num">{fmtHours(s.connected_hours)}</td>
                  <td className="num">{fmtHours(s.charging_hours)}</td>
                  <td className="num">{s.kwh_delivered.toFixed(1)} kWh</td>
                  <td>
                    {s.request_met === null ? (
                      <span className="muted">No request</span>
                    ) : s.request_met ? (
                      "Met"
                    ) : (
                      <span className="short">Short {((s.kwh_requested ?? 0) - s.kwh_delivered).toFixed(1)} kWh</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {cursor && (
        <button className="quiet" onClick={more} disabled={busy}>
          {busy ? "Loading…" : `Show ${PAGE} more`}
        </button>
      )}
    </div>
  );
}
