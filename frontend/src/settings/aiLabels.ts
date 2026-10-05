// Saved profile IDs remain stable so existing jobs and credentials keep their bindings.
export function connectionLabel(profile: string): string {
  return profile === "cloud"
    ? "Direct provider API"
    : profile === "local"
      ? "9router / compatible API"
      : profile;
}

export function reviewModelLabel(key: string, fallback: string): string {
  return key === "governance.cloud_model_tier"
    ? "Direct API review model"
    : key === "governance.local_model_tier"
      ? "9router review model"
      : fallback;
}

export function providerLabel(provider: string): string {
  const labels: Record<string, string> = {
    openai: "OpenAI",
    anthropic: "Anthropic",
    google: "Google",
    openrouter: "OpenRouter",
    openai_compatible: "OpenAI-compatible API",
    ollama: "Ollama",
  };
  return labels[provider] ?? provider;
}
