/** Keep account limits when pausing or enabling broker execution. */
export function brokerPolicyLimits(settings?: Record<string, unknown>) {
  if (settings?.no_owner_limits === true) return { no_owner_limits: true };
  if (settings?.no_owner_limits !== false) {
    throw new Error(
      "Save the account risk settings before changing its policy.",
    );
  }
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
