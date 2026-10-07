import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, beforeEach } from "vitest";
import { server } from "./server";

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
// The pending requests are asked by every admin screen that marks rows (T-033); a test that is
// about them registers its own handler, which wins over this empty list.
beforeEach(() => {
  server.use(
    http.get("*/api/v1/changes", () => HttpResponse.json({ items: [], next_cursor: null })),
  );
});
afterEach(() => {
  cleanup();
  server.resetHandlers();
  window.sessionStorage.clear();
});
afterAll(() => server.close());
