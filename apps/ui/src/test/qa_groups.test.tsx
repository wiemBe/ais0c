import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { groupDetail, problem, qaItem } from "./fixtures";
import { json, problemResponse, renderApp } from "./render";
import { server } from "./server";

const ITEM = "6f1c1d0e-0000-4000-8000-000000000001";

describe("QA queue (criterion 6)", () => {
  it("filters by status and reason", async () => {
    const queries: URLSearchParams[] = [];
    server.use(
      http.get("*/api/v1/qa", ({ request }) => {
        queries.push(new URL(request.url).searchParams);
        return json({ items: [qaItem()], next_cursor: null });
      }),
    );
    renderApp("/qa");
    await screen.findByText("Düşük güven");
    expect(queries[0]?.get("status")).toBe("open");
    await userEvent.selectOptions(screen.getByLabelText("QA nedeni"), "injection_suspected");
    await waitFor(() => expect(queries.at(-1)?.get("reason")).toBe("injection_suspected"));
    await userEvent.selectOptions(screen.getByLabelText("Durum"), "resolved");
    await waitFor(() => expect(queries.at(-1)?.get("status")).toBe("resolved"));
  });

  it("resolves an item with the feedback reason", async () => {
    const posted = vi.fn();
    server.use(
      http.get("*/api/v1/qa", () => json({ items: [qaItem()], next_cursor: null })),
      http.post(`*/api/v1/qa/${ITEM}/resolve`, async ({ request }) => {
        posted(await request.json());
        return json({ item: qaItem({ status: "resolved" }), feedback: {} });
      }),
    );
    renderApp("/qa");
    await userEvent.click(await screen.findByRole("button", { name: "Çöz" }));
    expect(screen.getByLabelText("Neden")).toBeInTheDocument(); // "geri bildirim nedeni" select
    await userEvent.selectOptions(screen.getByLabelText("Doğru karar"), "tp");
    await userEvent.selectOptions(screen.getByLabelText("Neden"), "correct");
    await userEvent.type(screen.getByLabelText(/Yorum/), "Onaylandı");
    await userEvent.click(screen.getAllByRole("button", { name: "Çöz" }).at(-1) as HTMLElement);
    expect(await screen.findByText("QA kaydı çözüldü.")).toBeInTheDocument();
    expect(posted).toHaveBeenCalledWith({ verdict: "tp", reason: "correct", comment: "Onaylandı" });
  });

  it("says someone else resolved it on a 409", async () => {
    server.use(
      http.get("*/api/v1/qa", () => json({ items: [qaItem()], next_cursor: null })),
      http.post(`*/api/v1/qa/${ITEM}/resolve`, () =>
        problemResponse(problem("qa.already_resolved", 409), 409),
      ),
    );
    renderApp("/qa");
    await userEvent.click(await screen.findByRole("button", { name: "Çöz" }));
    await userEvent.selectOptions(screen.getByLabelText("Doğru karar"), "tp");
    await userEvent.selectOptions(screen.getByLabelText("Neden"), "correct");
    await userEvent.click(screen.getAllByRole("button", { name: "Çöz" }).at(-1) as HTMLElement);
    expect(await screen.findByText("Bu kaydı başkası çözdü.")).toBeInTheDocument();
  });
});

describe("groups (criterion 7)", () => {
  it("lists the groups and filters by status", async () => {
    const queries: URLSearchParams[] = [];
    server.use(
      http.get("*/api/v1/groups", ({ request }) => {
        queries.push(new URL(request.url).searchParams);
        return json({ items: [groupDetail().group], next_cursor: null });
      }),
    );
    renderApp("/groups");
    expect(await screen.findByRole("link", { name: "group-g1" })).toBeInTheDocument();
    expect(screen.getAllByText("Fırtına").length).toBeGreaterThan(0);
    await userEvent.selectOptions(screen.getByLabelText("Durum"), "storm");
    await waitFor(() => expect(queries.at(-1)?.get("status")).toBe("storm"));
  });

  it("shows the decision, the summary and the offenses of a group", async () => {
    server.use(http.get("*/api/v1/groups/group-g1", () => json(groupDetail())));
    renderApp("/groups/group-g1");
    expect(await screen.findByText(/Deterministik özet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Grup vakasını aç" })).toHaveAttribute(
      "href",
      "/cases/group-g1",
    );
    expect(screen.getByText("Şüpheli")).toBeInTheDocument();
    // the deterministic summary
    expect(screen.getByText("Kaynak IP")).toBeInTheDocument();
    expect(screen.getByText(/198\.51\.100\.7 \(2\)/)).toBeInTheDocument();
    // each offense with its full analysis reason and its QRadar link
    expect(screen.getByText("Yeni log source veya kategori")).toBeInTheDocument();
    expect(screen.getByText("Tam analiz yok")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "5001" })).toHaveAttribute(
      "href",
      "https://192.0.2.10/console/ui/offenses/5001",
    );
  });

  it("shows a group that has no case yet", async () => {
    server.use(
      http.get("*/api/v1/groups/group-g1", () =>
        json(groupDetail({ case_id: null, case_status: null, verdict: null, notify_level: null })),
      ),
    );
    renderApp("/groups/group-g1");
    expect(await screen.findByText("Grubun henüz vakası yok.")).toBeInTheDocument();
  });
});
