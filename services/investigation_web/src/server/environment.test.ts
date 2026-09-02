import { describe, expect, it } from "vitest";

import { getAgentBaseUrl } from "./environment";

describe("getAgentBaseUrl", () => {
  it("normalizes a valid absolute HTTP URL", () => {
    expect(getAgentBaseUrl({ INVESTIGATION_AGENT_URL: "http://agent:8080/" }).href).toBe(
      "http://agent:8080/",
    );
  });

  it.each([undefined, "relative/path", "file:///tmp/agent"])(
    "rejects an invalid value",
    (value) => {
      expect(() =>
        getAgentBaseUrl(value === undefined ? {} : { INVESTIGATION_AGENT_URL: value }),
      ).toThrow(/INVESTIGATION_AGENT_URL/);
    },
  );
});
