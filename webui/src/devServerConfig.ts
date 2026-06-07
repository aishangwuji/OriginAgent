const DEFAULT_DEV_SERVER_PORT = 4173;
const DEFAULT_HMR_PORT = 4174;
const MAX_PORT = 65535;

function parsePort(value: string | undefined, fallback: number): number {
  if (!value) {
    return fallback;
  }

  if (!/^\d+$/.test(value)) {
    return fallback;
  }

  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0 || parsed > MAX_PORT) {
    return fallback;
  }

  return parsed;
}

export function resolveDevServerPorts(env: Record<string, string | undefined>) {
  const port = parsePort(env.ORIGINAGENT_WEBUI_PORT, DEFAULT_DEV_SERVER_PORT);
  const defaultHmrPort = port < MAX_PORT ? port + 1 : DEFAULT_HMR_PORT;
  const requestedHmrPort = parsePort(env.ORIGINAGENT_WEBUI_HMR_PORT, defaultHmrPort);
  const hmrPort = requestedHmrPort === port ? defaultHmrPort : requestedHmrPort;

  return { port, hmrPort };
}
