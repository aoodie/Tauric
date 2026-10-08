import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  api,
  type Instrument,
  type Market,
  type Interval,
  type AnalysisStatus,
  type Analysis,
  type DataProvider,
} from "../api/client";

function useMarketState() {
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [providers, setProviders] = useState<DataProvider[]>([]);
  const [instrumentId, setInstrumentId] = useState("");
  const [interval, setIntervalValue] = useState<Interval>("15m");
  const [feed, setFeed] = useState<Market | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [auto, setAuto] = useState(true);
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [jobs, setJobs] = useState<Analysis[]>([]);
  const current = useRef("");
  current.current = `${instrumentId}/${interval}`;
  useEffect(() => {
    let active = true;
    api
      .instruments()
      .then((items) => {
        if (active) {
          setInstruments(items);
          setInstrumentId(
            items.find((i) => i.asset_type === "crypto_spot")?.id ??
              items[0]?.id ??
              "",
          );
        }
      })
      .catch((err) => {
        if (active) setError(err.message);
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    void api
      .marketProviders()
      .then(setProviders)
      .catch(() => setProviders([]));
  }, []);
  useEffect(() => {
    if (!instrumentId) return;
    let active = true;
    const key = `${instrumentId}/${interval}`;
    setFeed(null);
    setError("");
    const read = () =>
      api
        .market(instrumentId, interval)
        .then((next) => {
          if (active && current.current === key) setFeed(next);
        })
        .catch((err) => {
          if (active) setError(err.message);
        });
    void read();
    const timer = window.setInterval(read, 15000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [instrumentId, interval]);
  useEffect(() => {
    let active = true;
    const read = () =>
      Promise.all([api.analysisStatus(), api.analyses()])
        .then(([state, runs]) => {
          if (active) {
            setStatus(state);
            setJobs(runs);
          }
        })
        .catch(() => {
          if (active) setStatus(null);
        });
    void read();
    const timer = window.setInterval(read, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);
  const pending = useRef(false);
  async function refresh() {
    if (!instrumentId || pending.current) return;
    const key = current.current;
    pending.current = true;
    setLoading(true);
    setError("");
    try {
      const next = await api.refreshMarket(instrumentId, interval);
      if (current.current === key) setFeed(next);
    } catch (err) {
      if (current.current === key)
        setError(err instanceof Error ? err.message : "Market refresh failed.");
    } finally {
      pending.current = false;
      setLoading(false);
    }
  }
  useEffect(() => {
    if (!auto || !instrumentId) return;
    const update = () => {
      if (!document.hidden) void refresh();
    };
    update();
    const timer = window.setInterval(update, 30000);
    document.addEventListener("visibilitychange", update);
    window.addEventListener("focus", update);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", update);
      window.removeEventListener("focus", update);
    };
  }, [auto, instrumentId, interval]);
  async function reloadJobs() {
    setJobs(await api.analyses());
    setStatus(await api.analysisStatus());
  }
  async function reloadProviders() {
    setProviders(await api.marketProviders());
  }
  async function reloadInstruments() {
    const items = await api.instruments();
    setInstruments(items);
    return items;
  }
  return {
    instruments,
    providers,
    instrumentId,
    setInstrumentId,
    interval,
    setIntervalValue,
    feed,
    loading,
    error,
    auto,
    setAuto,
    refresh,
    status,
    jobs,
    reloadJobs,
    reloadProviders,
    reloadInstruments,
  };
}

const MarketContext = createContext<ReturnType<typeof useMarketState> | null>(
  null,
);
export function MarketProvider({ children }: { children: ReactNode }) {
  const state = useMarketState();
  return (
    <MarketContext.Provider value={state}>{children}</MarketContext.Provider>
  );
}
export function useMarket() {
  const state = useContext(MarketContext);
  if (!state) throw new Error("Market context is required");
  return state;
}
