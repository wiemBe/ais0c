import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { describe, expect, it, vi } from "vitest";
import type { CatalogLogSource, CatalogRule } from "../api/types";
import { ADMIN, problem } from "./fixtures";
import { json, problemResponse, renderApp } from "./render";
import { server } from "./server";

const rule = (overrides: Partial<CatalogRule> = {}): CatalogRule => ({
  rule_id: 100201,
  rule_name: "Excessive Firewall Accepts",
  defined: true,
  mode: "analyze",
  min_level: null,
  has_automated_action: false,
  context_note: null,
  qradar_enabled: true,
  attack_techniques: [],
  ai_draft_note: null,
  updated_at: "2026-10-02T10:00:00Z",
  updated_by: "sync",
  ...overrides,
});

const logSource = (overrides: Partial<CatalogLogSource> = {}): CatalogLogSource => ({
  log_source_id: 2001,
  name: "SRV-0001.example.com",
  type_name: "Linux OS",
  defined: false,
  in_scope: true,
  description: null,
  owner: null,
  criticality: null,
  context_note: null,
  qradar_enabled: true,
  default_telemetry_classes: ["linux"],
  telemetry_classes: null,
  effective_telemetry_classes: ["linux"],
  updated_at: "2026-10-02T10:00:00Z",
  updated_by: "sync",
  ...overrides,
});

describe("catalog (criterion 8)", () => {
  it("filters the rules", async () => {
    const queries: URLSearchParams[] = [];
    server.use(
      http.get("*/api/v1/catalog/rules", ({ request }) => {
        queries.push(new URL(request.url).searchParams);
        return json({ items: [rule()], next_cursor: null });
      }),
    );
    renderApp("/catalog", ADMIN);
    await screen.findByText("Excessive Firewall Accepts");
    await userEvent.selectOptions(screen.getByLabelText("Tanımlı"), "false");
    await userEvent.selectOptions(screen.getByLabelText("Mod"), "skip");
    await userEvent.selectOptions(screen.getByLabelText("QRadar'da açık"), "true");
    await userEvent.selectOptions(screen.getByLabelText("QRadar'da yok"), "true");
    await userEvent.type(screen.getByLabelText("Ara"), "firewall");
    await waitFor(() => {
      const last = queries.at(-1);
      expect(last?.get("defined")).toBe("false");
      expect(last?.get("mode")).toBe("skip");
      expect(last?.get("qradar_enabled")).toBe("true");
      expect(last?.get("missing")).toBe("true");
      expect(last?.get("q")).toBe("firewall");
    });
  });

  it("lets an admin edit a rule", async () => {
    const put = vi.fn();
    server.use(
      http.get("*/api/v1/catalog/rules", () => json({ items: [rule()], next_cursor: null })),
      http.put("*/api/v1/catalog/rules/100201", async ({ request }) => {
        put(await request.json());
        return json({ change_id: "c1" }, 202);
      }),
    );
    renderApp("/catalog", ADMIN);
    await userEvent.click(await screen.findByRole("button", { name: "Düzenle" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.selectOptions(within(dialog).getByLabelText("Mod"), "skip");
    await userEvent.selectOptions(within(dialog).getByLabelText("Taban seviye"), "high");
    await userEvent.type(within(dialog).getByLabelText(/ATT&CK/), "T1059, T1021");
    await userEvent.click(within(dialog).getByRole("button", { name: "Kaydet" }));
    // 202: the edit waits for a second admin and the dialog says so (T-033 criterion 5).
    const waiting = await screen.findByRole("dialog");
    expect(await within(waiting).findByText(/Onay bekliyor/)).toBeInTheDocument();
    await userEvent.click(within(waiting).getByRole("button", { name: "Kapat" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(put).toHaveBeenCalledWith({
      mode: "skip",
      min_level: "high",
      has_automated_action: false,
      context_note: null,
      attack_techniques: ["T1059", "T1021"],
    });
  });

  it("shows the refusal of an invalid rule", async () => {
    server.use(
      http.get("*/api/v1/catalog/rules", () => json({ items: [rule()], next_cursor: null })),
      http.put("*/api/v1/catalog/rules/100201", () =>
        problemResponse(problem("catalog.rule_invalid", 422), 422),
      ),
    );
    renderApp("/catalog", ADMIN);
    await userEvent.click(await screen.findByRole("button", { name: "Düzenle" }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Kaydet" }),
    );
    expect(await screen.findByText("Kural değerleri geçersiz.")).toBeInTheDocument();
  });

  it("accepts the AI draft", async () => {
    const accepted = vi.fn();
    server.use(
      http.get("*/api/v1/catalog/rules", () =>
        json({
          items: [rule({ ai_draft_note: "Güvenlik duvarı kabul sayısı kuralı" })],
          next_cursor: null,
        }),
      ),
      http.post("*/api/v1/catalog/rules/100201/accept-draft", () => {
        accepted();
        return json({ change_id: "c2" }, 202);
      }),
    );
    renderApp("/catalog", ADMIN);
    expect(await screen.findByText(/Güvenlik duvarı kabul sayısı kuralı/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Taslağı kabul et" }));
    expect(await screen.findByText(/Onay bekliyor/)).toBeInTheDocument();
    expect(accepted).toHaveBeenCalled();
  });

  it("starts the sync (202) and says it started", async () => {
    server.use(
      http.get("*/api/v1/catalog/rules", () => json({ items: [], next_cursor: null })),
      http.post("*/api/v1/catalog/sync", () =>
        json({ schedule_id: "knowledge-sync", triggered: true }, 202),
      ),
    );
    renderApp("/catalog", ADMIN);
    await userEvent.click(await screen.findByRole("button", { name: "Senkronu başlat" }));
    expect(await screen.findByText("Senkron başlatıldı.")).toBeInTheDocument();
  });

  it("shows why a sync could not start", async () => {
    server.use(
      http.get("*/api/v1/catalog/rules", () => json({ items: [], next_cursor: null })),
      http.post("*/api/v1/catalog/sync", () =>
        problemResponse(problem("temporal.unavailable", 503), 503),
      ),
    );
    renderApp("/catalog", ADMIN);
    await userEvent.click(await screen.findByRole("button", { name: "Senkronu başlat" }));
    expect(await screen.findByText(/İş akışı servisine şu an ulaşılamıyor/)).toBeInTheDocument();
  });

  it("edits a log source", async () => {
    const put = vi.fn();
    server.use(
      http.get("*/api/v1/catalog/rules", () => json({ items: [], next_cursor: null })),
      http.get("*/api/v1/catalog/log-sources", ({ request }) => {
        expect(new URL(request.url).searchParams.get("in_scope")).toBeNull();
        return json({ items: [logSource()], next_cursor: null });
      }),
      http.put("*/api/v1/catalog/log-sources/2001", async ({ request }) => {
        put(await request.json());
        return json({ change_id: "c3" }, 202);
      }),
    );
    renderApp("/catalog", ADMIN);
    await userEvent.click(await screen.findByRole("button", { name: "Log source'lar" }));
    await userEvent.click(await screen.findByRole("button", { name: "Düzenle" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Sahip/), "SOC Altyapı");
    await userEvent.selectOptions(within(dialog).getByLabelText("Kritiklik"), "critical");
    await userEvent.click(within(dialog).getByLabelText("Kapsamda"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Kaydet" }));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(put).toHaveBeenCalledWith({
      description: null,
      owner: "SOC Altyapı",
      criticality: "critical",
      in_scope: false,
      context_note: null,
    });
  });
});

describe("administration (criterion 8)", () => {
  const flag = (enabled: boolean) =>
    json([
      {
        name: "writes_enabled",
        enabled,
        changed_by: "ad-1",
        changed_at: "2026-10-02T10:00:00Z",
        reason: "Canary başlangıcı",
      },
    ]);

  it("shows the kill switch state, who changed it and why", async () => {
    server.use(http.get("*/api/v1/admin/platform-flags", () => flag(false)));
    server.use(
      http.get("*/api/v1/critical-assets", () => json([])),
      http.get("*/api/v1/notification-recipients", () => json({ allowed_domains: [], groups: [] })),
      http.get("*/api/v1/notification-routes", () => json([])),
    );
    renderApp("/admin", ADMIN);
    expect(await screen.findByText(/Yazmalar KAPALI/)).toBeInTheDocument();
    expect(screen.getByText(/ad-1/)).toBeInTheDocument();
    expect(screen.getByText(/Canary başlangıcı/)).toBeInTheDocument();
  });

  it("asks for a confirmation before it opens the writes", async () => {
    const put = vi.fn();
    server.use(
      http.get("*/api/v1/admin/platform-flags", () => flag(false)),
      http.get("*/api/v1/critical-assets", () => json([])),
      http.get("*/api/v1/notification-recipients", () => json({ allowed_domains: [], groups: [] })),
      http.get("*/api/v1/notification-routes", () => json([])),
      http.put("*/api/v1/admin/platform-flags/writes_enabled", async ({ request }) => {
        put(await request.json());
        return json({ change_id: "c4" }, 202);
      }),
    );
    renderApp("/admin", ADMIN);
    await screen.findByText(/Yazmalar KAPALI/);
    // no reason: nothing opens
    await userEvent.click(screen.getByRole("button", { name: "Yazmaları aç" }));
    expect(screen.getByText("Neden zorunludur.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Neden (zorunlu)"), "Canary");
    await userEvent.click(screen.getByRole("button", { name: "Yazmaları aç" }));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText(/İkinci bir admin onaylayınca yazmalar açılır/),
    ).toBeInTheDocument();
    expect(put).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Onayla" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ enabled: true, reason: "Canary" }));
    // 202: the writes stay closed until a second admin approves.
    expect(
      await screen.findByText(/yazmalar, ikinci bir admin onaylayınca açılır/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Yazmalar KAPALI/)).toBeInTheDocument();
  });

  it("closes the writes in one step", async () => {
    const put = vi.fn();
    server.use(
      http.get("*/api/v1/admin/platform-flags", () => flag(true)),
      http.get("*/api/v1/critical-assets", () => json([])),
      http.get("*/api/v1/notification-recipients", () => json({ allowed_domains: [], groups: [] })),
      http.get("*/api/v1/notification-routes", () => json([])),
      http.put("*/api/v1/admin/platform-flags/writes_enabled", async ({ request }) => {
        put(await request.json());
        return json({ name: "writes_enabled", enabled: false });
      }),
    );
    renderApp("/admin", ADMIN);
    await screen.findByText(/Yazmalar AÇIK/);
    await userEvent.type(screen.getByLabelText("Neden (zorunlu)"), "Acil durdurma");
    await userEvent.click(screen.getByRole("button", { name: "Yazmaları kapat" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith({ enabled: false, reason: "Acil durdurma" }),
    );
  });

  it("adds and deletes a critical asset", async () => {
    const added = vi.fn();
    const removed = vi.fn();
    const asset = { id: "a1", kind: "ip", value: "192.0.2.5", label: "DC", level: "critical" };
    server.use(
      http.get("*/api/v1/admin/platform-flags", () => flag(false)),
      http.get("*/api/v1/critical-assets", () => json([asset])),
      http.get("*/api/v1/notification-recipients", () => json({ allowed_domains: [], groups: [] })),
      http.get("*/api/v1/notification-routes", () => json([])),
      http.post("*/api/v1/critical-assets", async ({ request }) => {
        added(await request.json());
        return json({ change_id: "c5" }, 202);
      }),
      http.delete("*/api/v1/critical-assets/a1", () => {
        removed();
        return json({ change_id: "c6" }, 202);
      }),
    );
    renderApp("/admin", ADMIN);
    expect(await screen.findByText("192.0.2.5")).toBeInTheDocument();
    const form = screen.getByRole("button", { name: "Ekle" }).closest("form") as HTMLElement;
    await userEvent.selectOptions(within(form).getByLabelText("Tür"), "host");
    await userEvent.type(within(form).getByLabelText("Değer"), "dc01.example.com");
    await userEvent.type(within(form).getByLabelText("Etiket"), "Etki alanı denetleyicisi");
    await userEvent.selectOptions(within(form).getByLabelText("Seviye"), "high");
    await userEvent.click(within(form).getByRole("button", { name: "Ekle" }));
    await waitFor(() =>
      expect(added).toHaveBeenCalledWith({
        kind: "host",
        value: "dc01.example.com",
        label: "Etki alanı denetleyicisi",
        level: "high",
      }),
    );
    expect(
      await screen.findByText(/kritik varlık listesi, ikinci bir admin onaylayınca/),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sil" }));
    await waitFor(() => expect(removed).toHaveBeenCalled());
  });

  it("edits a recipient group and shows the refused domains of a 422", async () => {
    server.use(
      http.get("*/api/v1/admin/platform-flags", () => flag(false)),
      http.get("*/api/v1/critical-assets", () => json([])),
      http.get("*/api/v1/notification-routes", () => json([])),
      http.get("*/api/v1/notification-recipients", () =>
        json({
          allowed_domains: ["example.com"],
          groups: [{ list_name: "soc-critical", emails: ["a@example.com"] }],
        }),
      ),
      http.put("*/api/v1/notification-recipients/soc-critical", () =>
        problemResponse(
          problem("notification_recipients.domain_not_allowed", 422, { domains: ["evil.test"] }),
          422,
        ),
      ),
    );
    renderApp("/admin", ADMIN);
    const box = await screen.findByDisplayValue("a@example.com");
    await userEvent.clear(box);
    await userEvent.type(box, "x@evil.test");
    await userEvent.click(screen.getByRole("button", { name: "Grubu kaydet" }));
    expect(
      await screen.findByText("Bir veya daha fazla adresin alan adı izinli değil."),
    ).toBeInTheDocument();
    expect(screen.getByText(/evil\.test/, { selector: "p" })).toBeInTheDocument();
  });

  it("says a group in use cannot be emptied (409)", async () => {
    server.use(
      http.get("*/api/v1/admin/platform-flags", () => flag(false)),
      http.get("*/api/v1/critical-assets", () => json([])),
      http.get("*/api/v1/notification-routes", () => json([])),
      http.get("*/api/v1/notification-recipients", () =>
        json({
          allowed_domains: ["example.com"],
          groups: [{ list_name: "soc-critical", emails: ["a@example.com"] }],
        }),
      ),
      http.put("*/api/v1/notification-recipients/soc-critical", () =>
        problemResponse(problem("notification_recipients.group_in_use", 409), 409),
      ),
    );
    renderApp("/admin", ADMIN);
    await userEvent.clear(await screen.findByDisplayValue("a@example.com"));
    await userEvent.click(screen.getByRole("button", { name: "Grubu kaydet" }));
    expect(
      await screen.findByText("Bu grup bir yönlendirmede kullanılıyor; boşaltılamaz."),
    ).toBeInTheDocument();
  });

  it("saves the routing table", async () => {
    const put = vi.fn();
    server.use(
      http.get("*/api/v1/admin/platform-flags", () => flag(false)),
      http.get("*/api/v1/critical-assets", () => json([])),
      http.get("*/api/v1/notification-recipients", () =>
        json({
          allowed_domains: ["example.com"],
          groups: [{ list_name: "soc-critical", emails: ["a@example.com"] }],
        }),
      ),
      http.get("*/api/v1/notification-routes", () =>
        json([{ kind: "case_alert", level: "critical", list_name: "soc-critical" }]),
      ),
      http.put("*/api/v1/notification-routes", async ({ request }) => {
        put(await request.json());
        return json([]);
      }),
    );
    renderApp("/admin", ADMIN);
    const high = await screen.findByLabelText("Vaka uyarısı Yüksek soc-critical");
    await userEvent.click(high);
    await userEvent.click(screen.getByRole("button", { name: "Yönlendirmeyi kaydet" }));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(put).toHaveBeenCalledWith({
      routes: [
        { kind: "case_alert", level: "high", list_name: "soc-critical" },
        { kind: "case_alert", level: "critical", list_name: "soc-critical" },
      ],
    });
  });
});

describe("SLA (criterion 8)", () => {
  it("shows the table for a date range", async () => {
    const queries: URLSearchParams[] = [];
    server.use(
      http.get("*/api/v1/metrics/sla", ({ request }) => {
        queries.push(new URL(request.url).searchParams);
        return json({
          from: "2026-10-01T00:00:00Z",
          to: "2026-10-02T00:00:00Z",
          buckets: [
            {
              floor_level: "high",
              total: 7,
              on_time: 5,
              late: 1,
              undecided: 0,
              running: 1,
              closed: 0,
            },
            {
              floor_level: "none",
              total: 2,
              on_time: 2,
              late: 0,
              undecided: 0,
              running: 0,
              closed: 0,
            },
          ],
        });
      }),
    );
    renderApp("/sla");
    expect(await screen.findByText("Yüksek")).toBeInTheDocument();
    expect(screen.getByText("Seviye yok")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Başlangıç"), "2026-10-01");
    await waitFor(() => expect(queries.at(-1)?.get("from")).toBe("2026-10-01T00:00:00+03:00"));
  });
});
