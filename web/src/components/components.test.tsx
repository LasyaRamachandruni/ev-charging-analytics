import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { LoadProfile, Session, Station } from "../api/client";
import { LoadCurve } from "./LoadCurve";
import { Sessions } from "./Sessions";
import { Stations } from "./Stations";

function profile(): LoadProfile {
  const slots = Array.from({ length: 96 }, (_, slot) => ({
    slot,
    time: `${String(Math.floor(slot / 4)).padStart(2, "0")}:${String((slot % 4) * 15).padStart(2, "0")}`,
    weekday_avg_kw: slot === 38 ? 120 : 10,
    weekday_max_kw: slot === 38 ? 150 : 20,
    weekend_avg_kw: 5,
    weekend_max_kw: 8,
  }));
  return { site: "caltech", start: "2019-01-07", end: "2019-01-20", weekdays: 10, weekend_days: 4, slots };
}

test("load curve names the weekday peak", () => {
  render(<LoadCurve profile={profile()} />);
  expect(screen.getByRole("img")).toHaveAccessibleName("Weekday average power peaks at 120 kW around 9:30 am.");
  expect(screen.getByText(/average peak 120 kW at 9:30 am/)).toBeInTheDocument();
});

const station = (id: string, occupied: number, charging: number, kwh: number): Station => ({
  station_id: id,
  space_id: null,
  sessions: 10,
  total_kwh: kwh,
  occupied_share: occupied,
  charging_share: charging,
  idle_share_of_occupied: (occupied - charging) / occupied,
  first_session_date: "2019-01-07",
  last_session_date: "2019-01-20",
});

test("stations sort by occupancy, idle share or energy", async () => {
  const rows = [station("A", 0.2, 0.15, 100), station("B", 0.3, 0.05, 50), station("C", 0.1, 0.02, 300)];
  render(<Stations stations={rows} />);
  const order = () => screen.getAllByRole("rowheader").map((h) => h.textContent);
  expect(order()).toEqual(["B", "A", "C"]);
  await userEvent.selectOptions(screen.getByLabelText("Sort by"), "energy");
  expect(order()).toEqual(["C", "A", "B"]);
  await userEvent.selectOptions(screen.getByLabelText("Sort by"), "idle");
  expect(order()).toEqual(["B", "C", "A"]);
});

function session(i: number): Session {
  return {
    session_id: `s${i}`,
    station_id: "A",
    user_id: null,
    connected_at_utc: "2019-01-10T16:00:00Z",
    connected_at_local: "2019-01-10T08:00:00",
    disconnected_at_utc: "2019-01-10T22:00:00Z",
    connected_hours: 6,
    charging_hours: 2,
    idle_hours: 4,
    kwh_delivered: 10,
    kwh_requested: 12,
    request_met: false,
    overstayed: true,
  };
}

test("session log follows the cursor when showing more", async () => {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      const second = url.includes("cursor=");
      const body = {
        site: "caltech",
        start: "2019-01-07",
        end: "2019-01-20",
        items: (second ? [50, 51] : Array.from({ length: 50 }, (_, i) => i)).map(session),
        next_cursor: second ? null : "abc",
      };
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
  render(
    <Sessions site="caltech" range={{ start: "2019-01-07", end: "2019-01-20" }} stations={[]} station="" onStation={() => {}} total={52} />,
  );
  expect(await screen.findByText("Showing 50 of 52 sessions")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Show 50 more" }));
  await waitFor(() => expect(screen.getByText("Showing 52 of 52 sessions")).toBeInTheDocument());
  expect(calls[1]).toContain("cursor=abc");
  expect(screen.queryByRole("button", { name: /more/ })).not.toBeInTheDocument();
  expect(screen.getAllByText("Short 2.0 kWh")).toHaveLength(52);
  vi.unstubAllGlobals();
});
