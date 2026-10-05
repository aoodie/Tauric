import type { components } from "../api/schema";
import { client, unwrap } from "../api/client";

export type Dataset = components["schemas"]["DatasetView"];
export type Replay = components["schemas"]["ReplayView"];
export type Parameters = components["schemas"]["ReplayParameters"];
export type Research = components["schemas"]["ResearchState"];
export type Analytics = components["schemas"]["AnalyticsView"];
export type Metrics = components["schemas"]["OutcomeMetrics"];
export type Point = components["schemas"]["EquityPoint"];

export const researchApi = {
  strategies: async () => unwrap(await client.GET("/api/v1/strategies")),
  state: async (offset = 0) =>
    unwrap(
      await client.GET("/api/v1/research", { params: { query: { offset } } }),
    ),
  capture: async (body: components["schemas"]["DatasetCapture"]) =>
    unwrap(await client.POST("/api/v1/research/datasets/capture", { body })),
  import: async (body: components["schemas"]["DatasetImport"]) =>
    unwrap(await client.POST("/api/v1/research/datasets/import", { body })),
  run: async (body: components["schemas"]["ReplayCreate"]) =>
    unwrap(await client.POST("/api/v1/research/runs", { body })),
  analytics: async () => unwrap(await client.GET("/api/v1/analytics")),
};
