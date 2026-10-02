import { useEffect, useMemo, useState } from "react";
import { api, type Range, type Site, type Summary } from "./api/client";
import { DailyEnergy } from "./components/DailyEnergy";
import { LoadCurve } from "./components/LoadCurve";
import { Sessions } from "./components/Sessions";
import { Stations } from "./components/Stations";
import { fmtHours, fmtInt, fmtKw, fmtKwh, fmtLocalTime, fmtPct, fmtRange } from "./lib/format";
import { useFetch, type Loadable } from "./lib/useFetch";

// View state lives in the URL so any view can be bookmarked or shared.
function readUrl() {
  const q = new URLSearchParams(window.location.search);
  return { site: q.get("site") ?? "", start: q.get("start") ?? "", end: q.get("end") ?? "", station: q.get("station") ?? "" };
}

function Status<T>({ state, children }: { state: Loadable<T>; children: (data: T) => React.ReactNode }) {
  if (state.status === "loading") return <div className="placeholder" aria-busy="true" />;
  if (state.status === "error") return <p className="error">Couldn't load this section: {state.error.message}</p>;
  return <>{children(state.data)}</>;
}

export default function App() {
  const initial = useMemo(readUrl, []);
  const sites = useFetch((s) => api.sites(s), []);
  const [siteId, setSiteId] = useState(initial.site);
  const [start, setStart] = useState(initial.start);
  const [end, setEnd] = useState(initial.end);
  const [station, setStation] = useState(initial.station);

  const site: Site | undefined =
    sites.status === "ready" ? (sites.data.find((s) => s.site === siteId) ?? sites.data[0]) : undefined;

  useEffect(() => {
    if (!site) return;
    const q = new URLSearchParams({ site: site.site });
    if (start) q.set("start", start);
    if (end) q.set("end", end);
    if (station) q.set("station", station);
    window.history.replaceState(null, "", `?${q}`);
  }, [site, start, end, station]);

  if (sites.status === "loading") return <main className="page"><div className="placeholder tall" aria-busy="true" /></main>;
  if (sites.status === "error")
    return (
      <main className="page">
        <h1>The API isn't responding</h1>
        <p className="error">{sites.error.message}. Start it with <code>evcharge serve</code> and reload.</p>
      </main>
    );
  if (!site)
    return (
      <main className="page">
        <h1>No charging data yet</h1>
        <p>Run <code>evcharge run</code> to build the warehouse, then reload.</p>
      </main>
    );

  const range: Range = { start: start || site.first_date, end: end || site.last_date };
  return (
    <Dashboard
      sites={sites.data}
      site={site}
      range={range}
      station={station}
      onSite={(s) => {
        setSiteId(s);
        setStart("");
        setEnd("");
        setStation("");
      }}
      onRange={(r) => {
        setStart(r.start ?? "");
        setEnd(r.end ?? "");
      }}
      onStation={setStation}
    />
  );
}

interface DashboardProps {
  sites: Site[];
  site: Site;
  range: Range;
  station: string;
  onSite: (s: string) => void;
  onRange: (r: Range) => void;
  onStation: (s: string) => void;
}

function Dashboard({ sites, site, range, station, onSite, onRange, onStation }: DashboardProps) {
  const id = site.site;
  const summary = useFetch((s) => api.summary(id, range, s), [id, range.start, range.end]);
  const profile = useFetch((s) => api.loadProfile(id, range, s), [id, range.start, range.end]);
  const daily = useFetch((s) => api.daily(id, range, s), [id, range.start, range.end]);
  const stations = useFetch((s) => api.stations(id, s), [id]);
  const whole = range.start === site.first_date && range.end === site.last_date;

  return (
    <>
      <header className="bar">
        <p className="product">EV charging analytics</p>
        <form className="controls" onSubmit={(e) => e.preventDefault()}>
          <label>
            Site
            <select value={id} onChange={(e) => onSite(e.target.value)}>
              {sites.map((s) => (
                <option key={s.site} value={s.site}>
                  {s.site}
                </option>
              ))}
            </select>
          </label>
          <label>
            From
            <input
              type="date"
              value={range.start}
              min={site.first_date}
              max={range.end}
              onChange={(e) => e.target.value && onRange({ ...range, start: e.target.value })}
            />
          </label>
          <label>
            To
            <input
              type="date"
              value={range.end}
              min={range.start}
              max={site.last_date}
              onChange={(e) => e.target.value && onRange({ ...range, end: e.target.value })}
            />
          </label>
          {!whole && (
            <button type="button" className="quiet" onClick={() => onRange({})}>
              Whole history
            </button>
          )}
        </form>
      </header>

      <main className="page">
        <section className="hero" aria-labelledby="site-name">
          <h1 id="site-name">{id}</h1>
          <p className="lede">
            {fmtInt(site.stations)} stations, {fmtRange(range.start!, range.end!)}
          </p>
          {id === "synthetic" && (
            <p className="note">
              Synthetic sessions generated to exercise the pipeline. They are not real measurements. Run on ACN-Data to see a real site.
            </p>
          )}
          <h2 className="chart-title">Site power through the day</h2>
          <Status state={profile}>{(p) => <LoadCurve profile={p} />}</Status>
        </section>

        <Status state={summary}>{(s) => <Figures s={s} />}</Status>

        <section aria-labelledby="daily">
          <h2 id="daily">Energy delivered each day</h2>
          <p className="section-note">Weekends in amber.</p>
          <Status state={daily}>{(rows) => <DailyEnergy rows={rows} />}</Status>
        </section>

        <section aria-labelledby="stations">
          <h2 id="stations">Where chargers sit occupied but idle</h2>
          <p className="section-note">
            Over the site's whole history. A car that finishes charging and stays parked blocks the next driver. Pick a station to see its sessions.
          </p>
          <Status state={stations}>
            {(rows) => (
              <Stations
                stations={rows}
                onPick={(sid) => {
                  onStation(sid);
                  document.getElementById("sessions")?.scrollIntoView({ behavior: "smooth" });
                }}
              />
            )}
          </Status>
        </section>

        <section aria-labelledby="sessions">
          <h2 id="sessions">Sessions</h2>
          <Sessions
            site={id}
            range={range}
            stations={stations.status === "ready" ? stations.data : []}
            station={station}
            onStation={onStation}
            total={summary.status === "ready" ? summary.data.sessions : undefined}
          />
        </section>
      </main>
    </>
  );
}

function Figures({ s }: { s: Summary }) {
  const figures: { value: string; label: string; detail?: string }[] = [
    { value: fmtInt(s.sessions), label: "charging sessions", detail: `${fmtInt(s.identified_users)} identified drivers` },
    { value: fmtKwh(s.energy_kwh), label: "delivered" },
    {
      value: s.peak_kw == null ? "–" : fmtKw(s.peak_kw),
      label: "highest 15-minute demand",
      detail: s.peak_at ? fmtLocalTime(s.peak_at) : undefined,
    },
    { value: fmtHours(s.avg_connected_hours), label: "average time plugged in", detail: `${fmtHours(s.avg_idle_hours)} of it after charging finished` },
    { value: fmtPct(s.share_requests_met), label: "of driver requests fully met" },
  ];
  return (
    <dl className="figures">
      {figures.map((f) => (
        <div key={f.label}>
          <dt>{f.label}</dt>
          <dd className="value">{f.value}</dd>
          {f.detail && <dd className="detail">{f.detail}</dd>}
        </div>
      ))}
    </dl>
  );
}
