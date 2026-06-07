import { describe, expect, it } from "vitest";

import { resolveDevServerPorts } from "../devServerConfig";

describe("resolveDevServerPorts", () => {
  it("uses safe default ports that avoid common Windows reserved ranges", () => {
    expect(resolveDevServerPorts({})).toEqual({ port: 4173, hmrPort: 4174 });
  });

  it("honors explicit environment overrides", () => {
    expect(
      resolveDevServerPorts({
        ORIGINAGENT_WEBUI_PORT: "5273",
        ORIGINAGENT_WEBUI_HMR_PORT: "5274",
      }),
    ).toEqual({ port: 5273, hmrPort: 5274 });
  });

  it("falls back when environment overrides are invalid", () => {
    expect(
      resolveDevServerPorts({
        ORIGINAGENT_WEBUI_PORT: "abc",
        ORIGINAGENT_WEBUI_HMR_PORT: "-1",
      }),
    ).toEqual({ port: 4173, hmrPort: 4174 });
  });

  it("defaults HMR to the next port when only the main port is overridden", () => {
    expect(
      resolveDevServerPorts({
        ORIGINAGENT_WEBUI_PORT: "5273",
      }),
    ).toEqual({ port: 5273, hmrPort: 5274 });
  });

  it("keeps HMR on a dedicated port when it matches the main port", () => {
    expect(
      resolveDevServerPorts({
        ORIGINAGENT_WEBUI_PORT: "5273",
        ORIGINAGENT_WEBUI_HMR_PORT: "5273",
      }),
    ).toEqual({ port: 5273, hmrPort: 5274 });
  });

  it("falls back to the next port when only the HMR override is invalid", () => {
    expect(
      resolveDevServerPorts({
        ORIGINAGENT_WEBUI_PORT: "5273",
        ORIGINAGENT_WEBUI_HMR_PORT: "bad-port",
      }),
    ).toEqual({ port: 5273, hmrPort: 5274 });
  });

  it("rejects partial or out-of-range values", () => {
    expect(
      resolveDevServerPorts({
        ORIGINAGENT_WEBUI_PORT: "5173abc",
        ORIGINAGENT_WEBUI_HMR_PORT: "70000",
      }),
    ).toEqual({ port: 4173, hmrPort: 4174 });
  });
});
