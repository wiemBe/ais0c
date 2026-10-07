import { render } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { App } from "../App";
import type { Me } from "../api/types";
import { OPERATOR } from "./fixtures";
import { server } from "./server";

/** Renders the app signed in as `me` at `path`, with `/me` answered. */
export function renderApp(path = "/", me: Me = OPERATOR) {
  window.sessionStorage.setItem("ais0c.token", "synthetic-token");
  server.use(http.get("*/api/v1/me", () => HttpResponse.json(me)));
  return render(<App initialPath={path} />);
}

export const json = (body: unknown, status = 200) => HttpResponse.json(body as object, { status });
export const problemResponse = (body: object, status: number) =>
  HttpResponse.json(body, { status, headers: { "Content-Type": "application/problem+json" } });
