export const timeframeLabels: Record<string, string> = {
  "1m": "1 minute",
  "5m": "5 minutes",
  "15m": "15 minutes",
  "1h": "1 hour",
  "4h": "4 hours",
  "1d": "1 day",
};
export const opportunityLabels: Record<string, string> = {
  candidate: "Ready to review",
  watch: "Watching",
  ignored: "Dismissed",
  expired: "Expired",
  inactive: "Scanner paused",
  invalidated: "No longer valid",
  escalated: "Sent to Tauric",
};
export function assetLabel(value: string) {
  return (
    (
      {
        crypto_spot: "Crypto",
        fx: "Forex",
        equity: "Stock",
        etf: "ETF",
      } as Record<string, string>
    )[value] ?? value
  );
}
