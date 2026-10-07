import { describe, expect, it } from "vitest";

// Model and QRadar text must never be interpreted as HTML (T-029 criterion 5): no component may
// use `dangerouslySetInnerHTML` (ESLint forbids it too).
const sources = import.meta.glob(["../**/*.ts", "../**/*.tsx", "!../test/**"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

describe("rendering of untrusted text", () => {
  it("never uses dangerouslySetInnerHTML or innerHTML", () => {
    const files = Object.entries(sources);
    expect(files.length).toBeGreaterThan(10);
    for (const [path, source] of files) {
      expect(source, path).not.toMatch(/dangerouslySetInnerHTML|innerHTML/);
    }
  });
});
