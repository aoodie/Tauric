import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { PortfolioPanel, PortfolioSelector } from "./Portfolios";

export function PortfolioSettings({
  open,
}: {
  open: (type: string, id: string) => void;
}) {
  const [items, setItems] = useState<components["schemas"]["PortfolioView"][]>(
    [],
  );
  const [id, setId] = useState("");
  const [error, setError] = useState("");
  const refresh = () => {
    void api
      .portfolios()
      .then(setItems)
      .catch((e) =>
        setError(e instanceof Error ? e.message : "Portfolios unavailable."),
      );
  };
  useEffect(refresh, []);
  return (
    <section>
      <h2>Portfolio mandates and chat</h2>
      {error && <p role="alert">{error}</p>}
      <PortfolioSelector value={id} onChange={setId} portfolios={items} />
      {id ? (
        <PortfolioPanel
          key={id}
          id={id}
          portfolios={items}
          changed={refresh}
          open={open}
        />
      ) : (
        <p className="muted">
          Choose a portfolio to manage its mandate, account links and chat
          permissions. Create allocations in Trading settings.
        </p>
      )}
    </section>
  );
}
