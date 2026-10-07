import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { describe, expect, it } from "vitest";
import { ADMIN, OPERATOR, caseSummary, problem } from "./fixtures";
import { json, problemResponse, renderApp } from "./render";
import { server } from "./server";
import { render } from "@testing-library/react";
import { App } from "../App";

describe("session (criterion 2)", () => {
  it("shows the login when there is no token", async () => {
    render(<App />);
    expect(await screen.findByLabelText("Token")).toBeInTheDocument();
    expect(screen.queryByText("Offense kuyruğu")).not.toBeInTheDocument();
  });

  it("keeps the pasted token in sessionStorage and sends it as a bearer token", async () => {
    let authorization: string | null = null;
    server.use(
      http.get("*/api/v1/me", ({ request }) => {
        authorization = request.headers.get("authorization");
        return json(OPERATOR);
      }),
      http.get("*/api/v1/cases", () => json({ items: [caseSummary()], next_cursor: null })),
    );
    render(<App />);
    await userEvent.type(await screen.findByLabelText("Token"), "pasted-token");
    await userEvent.click(screen.getByRole("button", { name: "Giriş yap" }));

    expect(await screen.findByText("case-100")).toBeInTheDocument();
    expect(authorization).toBe("Bearer pasted-token");
    expect(window.sessionStorage.getItem("ais0c.token")).toBe("pasted-token");
    expect(window.localStorage.length).toBe(0);
  });

  it("ends the session and returns to the login on a 401", async () => {
    server.use(
      http.get("*/api/v1/cases", () => problemResponse(problem("auth.unauthorized", 401), 401)),
    );
    renderApp("/");

    expect(await screen.findByText(/Oturum sona erdi/)).toBeInTheDocument();
    expect(screen.getByLabelText("Token")).toBeInTheDocument();
    expect(window.sessionStorage.getItem("ais0c.token")).toBeNull();
  });

  it("says the user is not allowed on a 403", async () => {
    server.use(
      http.get("*/api/v1/cases", () => problemResponse(problem("auth.forbidden", 403), 403)),
    );
    renderApp("/");
    expect(await screen.findByText("Bu işlem için yetkiniz yok.")).toBeInTheDocument();
    expect(screen.queryByText(/DETAIL-THE-USER/)).not.toBeInTheDocument();
  });

  it("hides the admin buttons from an operator", async () => {
    server.use(
      http.get("*/api/v1/admin/platform-flags", () =>
        json([{ name: "writes_enabled", enabled: false }]),
      ),
      http.get("*/api/v1/critical-assets", () => json([])),
      http.get("*/api/v1/catalog/rules", () =>
        json({
          items: [
            {
              rule_id: 100201,
              rule_name: "Excessive Firewall Accepts",
              defined: true,
              mode: "analyze",
              min_level: null,
              has_automated_action: false,
              context_note: null,
              qradar_enabled: true,
              updated_at: "2026-10-02T10:00:00Z",
              updated_by: "sync",
            },
          ],
          next_cursor: null,
        }),
      ),
    );
    const { unmount } = renderApp("/admin", OPERATOR);
    expect(await screen.findByText("Kill switch (AI yazmaları)")).toBeInTheDocument();
    await screen.findByText(/Yazmalar KAPALI/);
    expect(screen.queryByRole("button", { name: "Yazmaları aç" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ekle" })).not.toBeInTheDocument();
    expect(screen.queryByText("E-posta alıcı grupları")).not.toBeInTheDocument();
    unmount();

    renderApp("/catalog", OPERATOR);
    await screen.findByText("Excessive Firewall Accepts");
    expect(screen.queryByRole("button", { name: "Düzenle" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Senkronu başlat" })).not.toBeInTheDocument();
  });

  it("shows the admin buttons to an admin", async () => {
    server.use(
      http.get("*/api/v1/admin/platform-flags", () =>
        json([{ name: "writes_enabled", enabled: false }]),
      ),
      http.get("*/api/v1/critical-assets", () => json([])),
      http.get("*/api/v1/notification-recipients", () =>
        json({ allowed_domains: ["example.com"], groups: [] }),
      ),
      http.get("*/api/v1/notification-routes", () => json([])),
    );
    renderApp("/admin", ADMIN);
    expect(await screen.findByRole("button", { name: "Yazmaları aç" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("E-posta alıcı grupları")).toBeInTheDocument());
  });
});
