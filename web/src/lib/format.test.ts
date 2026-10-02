import { fmtDate, fmtHours, fmtKwh, fmtLocalTime, fmtPct, fmtRange } from "./format";
import { niceMax } from "./scale";

test("dates are not shifted by the viewer's timezone", () => {
  expect(fmtDate("2019-02-15")).toBe("Feb 15, 2019");
  expect(fmtRange("2019-01-07", "2019-05-06")).toBe("Jan 7 to May 6, 2019");
  expect(fmtRange("2018-12-20", "2019-01-03")).toBe("Dec 20, 2018 to Jan 3, 2019");
});

test("local wall-clock times keep the site's clock", () => {
  expect(fmtLocalTime("2019-02-15T09:45:00")).toBe("Fri Feb 15, 9:45 am");
  expect(fmtLocalTime("2019-02-17T00:05:00")).toBe("Sun Feb 17, 12:05 am");
  expect(fmtLocalTime("2019-02-17T12:30:00")).toBe("Sun Feb 17, 12:30 pm");
});

test("durations, energy and shares", () => {
  expect(fmtHours(6.75)).toBe("6 h 45 min");
  expect(fmtHours(0.5)).toBe("30 min");
  expect(fmtHours(2)).toBe("2 h");
  expect(fmtHours(null)).toBe("–");
  expect(fmtKwh(950.4)).toBe("950 kWh");
  expect(fmtKwh(48187)).toBe("48.2 MWh");
  expect(fmtPct(0.734)).toBe("73%");
});

test("axis maximum is a round number above the data", () => {
  expect(niceMax(131)).toEqual({ max: 150, step: 50 });
  expect(niceMax(0)).toEqual({ max: 1, step: 0.25 });
  const { max, step } = niceMax(723);
  expect(max).toBeGreaterThanOrEqual(723);
  expect(max % step).toBe(0);
});
