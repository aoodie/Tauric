/** Keep account limits when pausing or enabling broker execution. */
export function brokerPolicyLimits(settings?: Record<string, unknown>) {
  if (settings?.no_owner_limits !== false) return { no_owner_limits: true };
  if (
    typeof settings.maximum_risk_percent !== "string" ||
    typeof settings.maximum_drawdown_percent !== "string"
  ) {
    throw new Error(
      "Reload the account risk limits before changing its policy.",
    );
  }
  return {
    no_owner_limits: false,
    maximum_risk_percent: settings.maximum_risk_percent,
    maximum_drawdown_percent: settings.maximum_drawdown_percent,
  };
}
