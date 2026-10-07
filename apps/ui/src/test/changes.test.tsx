import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { describe, expect, it, vi } from "vitest";
import type { ChangeItem, CatalogRule } from "../api/types";
import { ADMIN, problem } from "./fixtures";
import { json, problemResponse, renderApp } from "./render";
import { server } from "./server";

// T-033 criterion 5: the "Bekleyen değişiklikler" screen of Yönetim, the 202 messages of the
// forms and the mark on a row that has a request waiting.

const SECOND_ADMIN = { ...ADMIN, subject: "ad-2", display_name: "Test Admin 2" };

const change = (overrides: Partial<ChangeItem> = {}): ChangeItem => ({
  id: "c1",
  object_type: "catalog_rule",
  object_id: "100201",
  object_version: "v1",
  change: {
    action: "update",
    before: {
      mode: "analyze",
      min_level: null,
      has_automated_action: false,
      context_note: null,
      attack_techniques: [],
    },
    after: {
      mode: "skip",
      min_level: "high",
      has_automated_action: false,
      context_note: "Tarama trafiği.",
      attack_techniques: ["T1003.006", "T1078"],
    },
  },
  requested_by: "ad-1",
  requested_at: "2026-10-07T10:00:00Z",
  decided_by: null,
  decided_at: null,
  status: "pending",
  reason: null,
  comment: null,
  ...overrides,
});

const page = (...items: ChangeItem[]) => json({ items, next_cursor: null });

/** The Yönetim page's other requests, answered empty. */
function adminBackground() {
  server.use(
    http.get("*/api/v1/admin/platform-flags", () =>
      json([{ name: "writes_enabled", enabled: false }]),
    ),
    http.get("*/api/v1/critical-assets", () => json([])),
    http.get("*/api/v1/notification-recipients", () => json({ allowed_domains: [], groups: [] })),
    http.get("*/api/v1/notification-routes", () => json([])),
  );
}

describe("pending changes (T-033 criterion 5)", () => {
  it("lists the requests and shows the values before and after side by side", async () => {
    const queries: URLSearchParams[] = [];
    adminBackground();
    server.use(
      http.get("*/api/v1/changes", ({ request }) => {
        queries.push(new URL(request.url).searchParams);
        return page(change());
      }),
    );
    renderApp("/admin", SECOND_ADMIN);

    expect(await screen.findByRole("heading", { name: "Bekleyen değişiklikler" })).toBeVisible();
    const table = (await screen.findByText("Katalog kuralı")).closest("table") as HTMLElement;
    expect(within(table).getByText("100201")).toBeInTheDocument();
    expect(within(table).getByText("ad-1")).toBeInTheDocument();
    // The list asks for the pending ones by default.
    expect(queries.some((query) => query.get("status") === "pending")).toBe(true);

    await userEvent.click(within(table).getByRole("button", { name: "Değişiklik isteği" }));
    const dialog = screen.getByRole("dialog");
    const row = within(dialog).getByText("Taban seviye").closest("tr") as HTMLElement;
    // Previous and requested values in one row.
    expect(
      within(row)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["Taban seviye", "—", "Yüksek"]);
    const mode = within(dialog).getByText("Mod").closest("tr") as HTMLElement;
    expect(
      within(mode)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["Mod", "Analiz et", "Atla"]);
    expect(within(dialog).getByText("T1003.006, T1078")).toBeInTheDocument();
  });

  it("approves a request another admin made", async () => {
    const approved = vi.fn();
    adminBackground();
    server.use(
      http.get("*/api/v1/changes", () => page(change())),
      http.post("*/api/v1/changes/c1/approve", () => {
        approved();
        return json(change({ status: "approved", decided_by: "ad-2" }));
      }),
    );
    renderApp("/admin", SECOND_ADMIN);
    await userEvent.click(await screen.findByRole("button", { name: "Değişiklik isteği" }));

    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Onayla" }),
    );

    await waitFor(() => expect(approved).toHaveBeenCalled());
    expect(await screen.findByText("Değişiklik onaylandı ve uygulandı.")).toBeInTheDocument();
  });

  it("rejects with a comment", async () => {
    const body = vi.fn();
    adminBackground();
    server.use(
      http.get("*/api/v1/changes", () => page(change())),
      http.post("*/api/v1/changes/c1/reject", async ({ request }) => {
        body(await request.json());
        return json(change({ status: "rejected", reason: "rejected_by_admin" }));
      }),
    );
    renderApp("/admin", SECOND_ADMIN);
    await userEvent.click(await screen.findByRole("button", { name: "Değişiklik isteği" }));
    const dialog = screen.getByRole("dialog");

    await userEvent.type(within(dialog).getByLabelText(/Reddetme yorumu/), "Kural yanlış");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reddet" }));

    await waitFor(() => expect(body).toHaveBeenCalledWith({ comment: "Kural yanlış" }));
    expect(await screen.findByText(/İstek reddedildi/)).toBeInTheDocument();
  });

  it("shows no approve or reject button on the admin's own request, only a withdrawal", async () => {
    const withdrawn = vi.fn();
    adminBackground();
    server.use(
      http.get("*/api/v1/changes", () => page(change())),
      http.post("*/api/v1/changes/c1/withdraw", () => {
        withdrawn();
        return json(change({ status: "rejected", reason: "withdrawn" }));
      }),
    );
    renderApp("/admin", ADMIN);
    await userEvent.click(await screen.findByRole("button", { name: "Değişiklik isteği" }));
    const dialog = screen.getByRole("dialog");

    expect(within(dialog).queryByRole("button", { name: "Onayla" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Reddet" })).not.toBeInTheDocument();
    expect(within(dialog).getByText(/başka bir admin gerekir/)).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Geri çek" }));

    await waitFor(() => expect(withdrawn).toHaveBeenCalled());
    expect(await screen.findByText("İstek geri çekildi.")).toBeInTheDocument();
  });

  it("does not offer a withdrawal to the other admin", async () => {
    adminBackground();
    server.use(http.get("*/api/v1/changes", () => page(change())));
    renderApp("/admin", SECOND_ADMIN);
    await userEvent.click(await screen.findByRole("button", { name: "Değişiklik isteği" }));

    expect(
      within(screen.getByRole("dialog")).queryByRole("button", { name: "Geri çek" }),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["change.stale", 409, /Nesne istekten sonra değişti/],
    ["change.already_decided", 409, "Bu istek zaten karara bağlandı."],
    ["change.self_approval", 403, /Kendi isteğinizi onaylayamazsınız/],
  ])("shows the Turkish message of %s", async (code, status, message) => {
    adminBackground();
    server.use(
      http.get("*/api/v1/changes", () => page(change())),
      http.post("*/api/v1/changes/c1/approve", () =>
        problemResponse(problem(code, status), status),
      ),
    );
    renderApp("/admin", SECOND_ADMIN);
    await userEvent.click(await screen.findByRole("button", { name: "Değişiklik isteği" }));

    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Onayla" }),
    );

    expect(await screen.findByText(message)).toBeInTheDocument();
    // The API's `detail` is never shown.
    expect(screen.queryByText(/DETAIL-THE-USER-MUST-NOT-SEE/)).not.toBeInTheDocument();
  });

  it("shows a flag request with its reason and a decided one with its end", async () => {
    adminBackground();
    server.use(
      http.get("*/api/v1/changes", () =>
        page(
          change({
            id: "c2",
            object_type: "platform_flag",
            object_id: "writes_enabled",
            change: {
              action: "enable",
              before: { enabled: false },
              after: { enabled: true },
              reason: "Canary başlıyor.",
            },
          }),
          change({ id: "c3", status: "rejected", reason: "stale", object_id: "100305" }),
        ),
      ),
    );
    renderApp("/admin", SECOND_ADMIN);
    expect(await screen.findByText("Reddedildi: Nesne değişti")).toBeInTheDocument();
    const buttons = await screen.findAllByRole("button", { name: "Değişiklik isteği" });
    await userEvent.click(buttons[0] as HTMLElement);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Canary başlıyor.")).toBeInTheDocument();
    expect(within(dialog).getByText("Yazmalar açık")).toBeInTheDocument();
  });

  it("marks the rows that have a request waiting", async () => {
    const rule: CatalogRule = {
      rule_id: 100201,
      rule_name: "Excessive Firewall Accepts",
      defined: false,
      mode: "analyze",
      min_level: null,
      has_automated_action: false,
      context_note: null,
      qradar_enabled: true,
      attack_techniques: [],
      ai_draft_note: null,
      updated_at: "2026-10-02T10:00:00Z",
      updated_by: "sync",
    };
    server.use(
      http.get("*/api/v1/catalog/rules", () => json({ items: [rule], next_cursor: null })),
      http.get("*/api/v1/changes", () => page(change())),
    );
    renderApp("/catalog", ADMIN);

    const cell = (await screen.findByText("Excessive Firewall Accepts")).closest(
      "td",
    ) as HTMLElement;
    expect(await within(cell).findByText("Onay bekliyor")).toBeInTheDocument();
  });

  it("marks an asset that waits for its removal", async () => {
    const asset = { id: "a1", kind: "ip", value: "192.0.2.5", label: "DC", level: "critical" };
    adminBackground();
    server.use(
      http.get("*/api/v1/critical-assets", () => json([asset])),
      http.get("*/api/v1/changes", () =>
        page(change({ object_type: "critical_asset", object_id: "a1" })),
      ),
    );
    renderApp("/admin", ADMIN);

    const cell = (await screen.findByText("192.0.2.5")).closest("td") as HTMLElement;
    expect(await within(cell).findByText("Onay bekliyor")).toBeInTheDocument();
  });

  it("does not ask an operator for the requests", async () => {
    const asked = vi.fn();
    adminBackground();
    server.use(
      http.get("*/api/v1/changes", () => {
        asked();
        return page();
      }),
    );
    renderApp("/admin");

    await screen.findByText(/Alıcı grupları ve yönlendirme tablosunu yalnızca admin görür/);
    expect(asked).not.toHaveBeenCalled();
    expect(screen.queryByText("Bekleyen değişiklikler")).not.toBeInTheDocument();
  });
});
