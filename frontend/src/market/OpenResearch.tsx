import { useEffect, useState, type FormEvent } from "react";
import { MessageSquare } from "lucide-react";

export function OpenResearch({
  symbol,
  interval,
  ask,
}: {
  symbol: string;
  interval: string;
  ask: (draft: string) => void;
}) {
  const [market, setMarket] = useState(symbol);
  const [timeframe, setTimeframe] = useState(interval);
  useEffect(() => setMarket(symbol), [symbol]);
  useEffect(() => setTimeframe(interval), [interval]);

  function research(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const requested = market.trim();
    if (!requested) return;
    ask(
      `Research ${requested} on the ${timeframe.trim()} timeframe using available prices and cited original sources. Verify the exact requested pair or product before collecting data, keep spot, CFDs, futures and ETFs distinct, and explain any missing or delayed data. Use open research even when this market has no built-in daily report.`,
    );
  }

  return (
    <form
      className="open-research"
      aria-label="Open market research"
      onSubmit={research}
    >
      <h3>Research any pair or market</h3>
      <p>
        Explore currency pairs, metals, commodities and other markets with
        OpenClaw. No built-in daily report or saved instrument is required.
      </p>
      <label>
        Pair or market
        <input
          aria-label="Research pair or market"
          value={market}
          maxLength={120}
          placeholder="EUR/GBP, XAU/USD, silver futures…"
          required
          onChange={(event) => setMarket(event.target.value)}
        />
      </label>
      <label>
        Timeframe
        <input
          aria-label="Research timeframe"
          value={timeframe}
          maxLength={12}
          pattern="[1-9][0-9]*(s|m|h|d|w|mo|y)"
          title="Use a number and unit, such as 15m, 4h, 1d, 1w or 1mo."
          placeholder="15m, 4h, 1d, 1w, 1mo"
          required
          onChange={(event) => setTimeframe(event.target.value)}
        />
      </label>
      <button className="primary" type="submit" disabled={!market.trim()}>
        <MessageSquare size={14} />
        Ask OpenClaw
      </button>
      <p className="native-connection">
        Opens a research request in chat for you to send. Price history depends
        on the data source; OpenClaw can continue with source research where
        exact candles are unavailable.
      </p>
    </form>
  );
}
