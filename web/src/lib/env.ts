// Vite env-var boundary. Throws at module load if any required VITE_* var is
// missing — fail loud rather than ship a bundle that 404s every request.

function requireEnv(name: string): string {
  const v = (import.meta.env as Record<string, string | undefined>)[name];
  if (!v) {
    throw new Error(
      `Missing required Vite env var: ${name}. ` +
      `Copy web/.env.example to web/.env.production (or .env.development) and fill it in.`,
    );
  }
  return v;
}

export const env = {
  apiBaseUrl:    requireEnv("VITE_API_BASE_URL"),
  appDomain:     requireEnv("VITE_APP_DOMAIN"),
  streamBaseUrl: requireEnv("VITE_STREAM_BASE_URL"),
};
