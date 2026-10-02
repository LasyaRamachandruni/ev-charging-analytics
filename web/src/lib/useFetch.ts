import { useEffect, useState } from "react";

export type Loadable<T> = { status: "loading" } | { status: "error"; error: Error } | { status: "ready"; data: T };

/** Run `load` whenever `deps` change; aborts the previous request so stale responses never win. */
export function useFetch<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[]): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ status: "loading" });
  useEffect(() => {
    const ctrl = new AbortController();
    setState({ status: "loading" });
    load(ctrl.signal)
      .then((data) => setState({ status: "ready", data }))
      .catch((error: Error) => {
        if (!ctrl.signal.aborted) setState({ status: "error", error });
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
