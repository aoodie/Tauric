import type { components } from "../api/schema";

type StockCosts = components["schemas"]["AssetCosts"];

export function stockCostValues(f: FormData, asset: string): StockCosts | null {
  if (asset !== "equity" && asset !== "etf") return null;
  return {
    asset_type: asset,
    spread_bps: String(f.get("stock_spread_bps")),
    commission_per_unit: String(f.get("stock_commission_per_unit")),
    minimum_commission: String(f.get("stock_minimum_commission")),
    sell_tax_bps: String(f.get("stock_sell_tax_bps")),
    financing_daily_bps: null,
    triple_rollover_weekday: 2,
  };
}

export function DeploymentCosts({ asset }: { asset: string }) {
  if (asset === "fx")
    return (
      <div className="paper-card">
        <label>
          Forex daily financing assumption (bps)
          <input
            name="fx_financing_daily_bps"
            defaultValue="0"
            inputMode="decimal"
            required
            maxLength={40}
          />
        </label>
        <p className="muted">
          Positive values debit cash; negative values credit it. The fixed
          assumption uses opening notional and New York weekday rollover, with
          Wednesday counted three times, and settles at close. Research rates
          are not applied automatically.
        </p>
      </div>
    );
  if (asset !== "equity" && asset !== "etf") return null;
  return (
    <fieldset className="paper-card" aria-label="Stock paper costs">
      <legend>Stock paper costs · USD</legend>
      <div className="paper-form-grid">
        {[
          ["stock_spread_bps", "Stock spread assumption (bps)"],
          ["stock_commission_per_unit", "Commission per share (USD)"],
          ["stock_minimum_commission", "Minimum commission per fill (USD)"],
          ["stock_sell_tax_bps", "Sell-side tax assumption (bps)"],
        ].map(([name, label]) => (
          <label key={name}>
            {label}
            <input
              name={name}
              defaultValue="0"
              inputMode="decimal"
              required
              maxLength={30}
            />
          </label>
        ))}
      </div>
      <p className="muted">
        These are your simulation assumptions. Account fees and slippage also
        apply. Use whole-share steps of at least one. Fills require a fresh
        regular-session trade bar; delayed prices and closed sessions cannot
        fill orders.
      </p>
    </fieldset>
  );
}
