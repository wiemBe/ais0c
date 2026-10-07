import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { REFRESH_MS } from "../api/hooks";
import { istanbulDayEnd, istanbulDayStart, slaState } from "../format";
import { casesQuery, NO_FILTERS } from "../pages/CaseQueue";
import { caseSummary } from "./fixtures";
import { json, renderApp } from "./render";
import { server } from "./server";

describe("offense queue (criterion 4)", () => {
  it("shows level, verdict, confidence, SLA, offense and rule", async () => {
    server.use(
      http.get("*/api/v1/cases", () => json({ items: [caseSummary()], next_cursor: null })),
    );
    renderApp("/");
    const row = (await screen.findByText("case-100")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Yüksek")).toBeInTheDocument(); // notify level
    expect(within(row).getByText("Şüpheli")).toBeInTheDocument();
    expect(within(row).getByText("Orta")).toBeInTheDocument(); // confidence
    expect(within(row).getByText("Zamanında")).toBeInTheDocument();
    expect(within(row).getByText(/100201/)).toBeInTheDocument();
    expect(within(row).getByText(/Excessive Firewall Accepts/)).toBeInTheDocument();
  });

  it("shows every SLA state", async () => {
    const rows = [
      caseSummary({ case_id: "c-ok" }),
      caseSummary({ case_id: "c-late", decided_at: "2026-10-02T11:00:00Z" }),
      caseSummary({ case_id: "c-run", status: "running", decided_at: null }),
      caseSummary({ case_id: "c-over", status: "running", decided_at: null, sla_overdue: true }),
      caseSummary({ case_id: "c-und", status: "no_ai_decision", verdict: null }),
    ];
    server.use(http.get("*/api/v1/cases", () => json({ items: rows, next_cursor: null })));
    renderApp("/");
    await screen.findByText("c-ok");
    const state = (id: string) => within(screen.getByText(id).closest("tr") as HTMLElement);
    expect(state("c-ok").getByText("Zamanında")).toBeInTheDocument();
    expect(state("c-late").getByText("Gecikti")).toBeInTheDocument();
    expect(state("c-run").getAllByText("Sürüyor").length).toBeGreaterThan(0);
    expect(state("c-over").getByText("Gecikti")).toBeInTheDocument();
    expect(state("c-und").getByText("Kararsız")).toBeInTheDocument();
    expect(rows.map(slaState)).toEqual(["on_time", "late", "running", "late", "undecided"]);
  });

  it("turns the filters into the query", async () => {
    const queries: URLSearchParams[] = [];
    server.use(
      http.get("*/api/v1/cases", ({ request }) => {
        queries.push(new URL(request.url).searchParams);
        return json({ items: [], next_cursor: null });
      }),
    );
    renderApp("/");
    await waitFor(() => expect(queries.length).toBe(1));
    await userEvent.selectOptions(screen.getByLabelText("Durum"), "running");
    await userEvent.selectOptions(screen.getByLabelText("Bildirim seviyesi"), "critical");
    await userEvent.selectOptions(screen.getByLabelText("AI kararı"), "tp");
    await userEvent.selectOptions(screen.getByLabelText("Kaynak"), "group");
    await userEvent.type(screen.getByLabelText("Kural no"), "100201");
    await waitFor(() => {
      const last = queries.at(-1);
      expect(last?.get("status")).toBe("running");
      expect(last?.get("notify_level")).toBe("critical");
      expect(last?.get("verdict")).toBe("tp");
      expect(last?.get("source")).toBe("group");
      expect(last?.get("rule_id")).toBe("100201");
    });
  });

  it("builds date bounds as Istanbul days, `to` exclusive", () => {
    expect(istanbulDayStart("2026-10-07")).toBe("2026-10-07T00:00:00+03:00");
    expect(istanbulDayEnd("2026-10-31")).toBe("2026-11-01T00:00:00+03:00");
    const query = casesQuery({ ...NO_FILTERS, from: "2026-10-01", to: "2026-10-02", ruleId: "x" });
    expect(query.from).toBe("2026-10-01T00:00:00+03:00");
    expect(query.to).toBe("2026-10-03T00:00:00+03:00");
    expect(query.rule_id).toBeUndefined();
  });

  it("loads the next page with the cursor", async () => {
    const cursors: (string | null)[] = [];
    server.use(
      http.get("*/api/v1/cases", ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("cursor");
        cursors.push(cursor);
        return cursor
          ? json({ items: [caseSummary({ case_id: "case-second" })], next_cursor: null })
          : json({ items: [caseSummary({ case_id: "case-first" })], next_cursor: "abc" });
      }),
    );
    renderApp("/");
    await screen.findByText("case-first");
    await userEvent.click(screen.getByRole("button", { name: "Sonraki sayfa" }));
    expect(await screen.findByText("case-second")).toBeInTheDocument();
    expect(screen.getByText("case-first")).toBeInTheDocument();
    expect(cursors).toContain("abc");
    expect(screen.queryByRole("button", { name: "Sonraki sayfa" })).not.toBeInTheDocument();
  });

  it("refreshes every 15 seconds", async () => {
    expect(REFRESH_MS).toBe(15_000);
    let calls = 0;
    server.use(
      http.get("*/api/v1/cases", () => {
        calls += 1;
        return json({ items: [caseSummary()], next_cursor: null });
      }),
    );
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderApp("/");
      await screen.findByText("case-100");
      expect(calls).toBe(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(REFRESH_MS + 100);
      });
      await waitFor(() => expect(calls).toBe(2));
    } finally {
      vi.useRealTimers();
    }
  });
});
