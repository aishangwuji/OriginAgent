# OriginAgent webui

The browser front-end for the originagent gateway. It is built with Vite + React 18 +
TypeScript + Tailwind 3 + shadcn/ui, talks to the gateway over the WebSocket
multiplex protocol, and reads session metadata from the embedded REST surface
on the same port.

For the project overview, install guide, and general docs map, see the root
[`README.md`](../README.md).

## Just want to use the WebUI?

If you installed OriginAgent via `pip install originagent`, the WebUI is **already bundled** in the wheel. Enable the WebSocket channel in `~/.originagent/config.json` and run `originagent gateway` — see the root [`README.md`](../README.md#-webui) for the 3-step setup. You do **not** need anything in this directory.

This `webui/` tree is for people **hacking on the WebUI itself** (UI changes, new components, styling, etc.).

## Layout

```text
webui/                 source tree (this directory)
OriginAgent/web/dist/      build output served by the gateway
```

## Develop the WebUI (Vite HMR)

### 1. Install OriginAgent from source

From the repository root:

```bash
pip install -e .
```

> Editable installs intentionally **skip** the WebUI bundle step — Vite HMR is faster than rebuilding `dist/` on every change.

### 2. Enable the WebSocket channel

In `~/.originagent/config.json`:

```json
{ "channels": { "websocket": { "enabled": true } } }
```

### 3. Start the gateway

In one terminal:

```bash
originagent gateway
```

### 4. Start the WebUI dev server

In another terminal:

```bash
cd webui
bun install            # npm install also works
bun run dev
```

Then open `http://127.0.0.1:4173`.

By default the dev server proxies `/api`, `/webui`, `/auth`, and WebSocket traffic to `http://127.0.0.1:8765`.
The defaults are:

- WebUI dev server: `4173`
- Vite HMR socket: `4174`

If you set only `ORIGINAGENT_WEBUI_PORT`, the HMR socket automatically moves to the next port.

If you need different dev ports, override them with environment variables:

```bash
ORIGINAGENT_WEBUI_PORT=5173 ORIGINAGENT_WEBUI_HMR_PORT=5174 bun run dev
```

On PowerShell:

```powershell
$env:ORIGINAGENT_WEBUI_PORT = "5173"
$env:ORIGINAGENT_WEBUI_HMR_PORT = "5174"
bun run dev
```

Keep the HMR port different from the main dev-server port. If you omit `ORIGINAGENT_WEBUI_HMR_PORT`, Vite uses the next port automatically.

The dev server uses a fixed port (`strictPort: true`). If the chosen port is already in use or blocked by local OS policy, Vite exits instead of silently picking another port, so set the environment variables above to an allowed port pair and restart `bun run dev`.

If your gateway listens on a non-default port, point the dev server at it:

```bash
ORIGINAGENT_API_URL=http://127.0.0.1:9000 bun run dev
```

### Access from another device (LAN)

To use the WebUI from another device on the same network, set `host` to `"0.0.0.0"` and configure a `token` or `tokenIssueSecret` in `~/.originagent/config.json`:

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 8765,
      "tokenIssueSecret": "your-secret-here"
    }
  }
}
```

The gateway will refuse to start if `host` is `"0.0.0.0"` and neither `token` nor `tokenIssueSecret` is set.

Then open `http://<your-ip>:8765` on the other device. The WebUI will show an authentication form where you enter the secret. It is saved in your browser so you only need to enter it once.

## Build for packaged runtime

You usually do not need to run this by hand: `python -m build` invokes the WebUI build automatically when packaging the wheel.

If you want to preview the production bundle locally without rebuilding the wheel:

```bash
cd webui
bun run build          # writes to ../OriginAgent/web/dist
```

The gateway picks up the new bundle on the next restart.

## Test

```bash
cd webui
bun run test
```

## Acknowledgements

- [`agent-chat-ui`](https://github.com/langchain-ai/agent-chat-ui) for UI and
  interaction inspiration across the chat surface.
