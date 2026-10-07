import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { caseDetail, caseSummary, problem, urgentEvent } from "./fixtures";
import { json, problemResponse, renderApp } from "./render";
import { server } from "./server";

const detailHandler = (body: unknown) => http.get("*/api/v1/cases/case-100", () => json(body));
const stepsHandler = http.get("*/api/v1/cases/case-100/steps", () =>
  json([
    {
      step: {
        run_id: "r1",
        agent_id: "triage",
        agent_version: "1.0.0",
        model_alias: "soc-fast",
        prompt_version: "1",
        started_at: "2026-10-02T10:00:00Z",
        ended_at: "2026-10-02T10:01:00Z",
        status: "completed",
        tokens: 1200,
        tool_call_count: 1,
        duration_seconds: 60,
      },
      tool_calls: [
        {
          tool_id: "qradar.ariel_search",
          status: "ok",
          policy_decision: "allow",
          latency_ms: 420,
          deny_reason: null,
          evidence_id: "ev_1",
        },
      ],
    },
  ]),
);
const feedbackList = http.get("*/api/v1/cases/case-100/feedback", () => json([]));

describe("case detail (criterion 5)", () => {
  it("shows every part of a case with a report", async () => {
    server.use(detailHandler(caseDetail()), stepsHandler, feedbackList);
    renderApp("/cases/case-100");

    expect(
      await screen.findByText("Dış adresten iç sunucuya SMB bağlantısı kabul edilmiş."),
    ).toBeInTheDocument();
    // urgent events with checklist and AQL
    expect(screen.getByText(/SMB connection accepted/)).toBeInTheDocument();
    expect(screen.getByText("Kaynak IP'yi doğrula")).toBeInTheDocument();
    expect(screen.getByText(/SELECT \* FROM events WHERE sourceip/)).toBeInTheDocument();
    // evidence and the QRadar link of the offense
    expect(screen.getByText("ev_1")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "QRadar'da aç" });
    expect(links[0]).toHaveAttribute("href", "https://192.0.2.10/console/ui/offenses/100");
    expect(links[0]).toHaveAttribute("rel", "noopener noreferrer");
    // verification, data gaps, recommendations, notes and e-mails
    expect(screen.getByText("Doğrulayıcı raporla aynı fikirde.")).toBeInTheDocument();
    expect(screen.getByText(/FW-DMZ-01 · Ayrıştırılmamış/)).toBeInTheDocument();
    expect(screen.getByText("Daha fazla incele")).toBeInTheDocument();
    expect(screen.getByText(/Yazıldı/)).toBeInTheDocument();
    expect(screen.getByText(/soc@example.com/)).toBeInTheDocument();
    // agent steps
    expect(await screen.findByText("soc-fast")).toBeInTheDocument();
    expect(screen.getByText(/qradar.ariel_search · Başarılı · İzin/)).toBeInTheDocument();
  });

  it("copies the AQL", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    server.use(detailHandler(caseDetail()), stepsHandler, feedbackList);
    renderApp("/cases/case-100");
    await userEvent.click(await screen.findByRole("button", { name: "Kopyala" }));
    expect(writeText).toHaveBeenCalledWith("SELECT * FROM events WHERE sourceip = '203.0.113.7'");
  });

  it("shows a case without a report", async () => {
    server.use(
      detailHandler(
        caseDetail({
          case: caseSummary({ status: "running", verdict: null, decided_at: null }),
          report: null,
          urgent_events: [],
          recommendations: [],
          verification: null,
          data_gaps: [],
          evidence: [],
          notes: [],
          notifications: [],
        }),
      ),
      stepsHandler,
      feedbackList,
    );
    renderApp("/cases/case-100");
    expect(await screen.findByText("Bu vaka için rapor yok.")).toBeInTheDocument();
    expect(screen.getByText("Verification sonucu yok.")).toBeInTheDocument();
  });

  it("shows an undecided case", async () => {
    server.use(
      detailHandler(
        caseDetail({
          case: caseSummary({ status: "no_ai_decision", verdict: null, notify_level: null }),
          report: null,
          urgent_events: [],
          verification: null,
        }),
      ),
      stepsHandler,
      feedbackList,
    );
    renderApp("/cases/case-100");
    expect(await screen.findByText("AI bu vaka için karar veremedi.")).toBeInTheDocument();
    expect(screen.getAllByText("Kararsız").length).toBeGreaterThan(0);
  });

  it("shows model and QRadar text as text, never as HTML", async () => {
    const markup = "<img src=x onerror=alert(1)>";
    const base = caseDetail();
    const event = urgentEvent({ event_name: markup, reason: `<b>${markup}</b>` });
    server.use(
      detailHandler({
        ...base,
        case: caseSummary({ offense_description: markup }),
        report: base.report && { ...base.report, summary_tr: markup, urgent_events: [event] },
        urgent_events: [event],
      }),
      stepsHandler,
      feedbackList,
    );
    const { container } = renderApp("/cases/case-100");
    await screen.findAllByText(new RegExp(markup.replace(/[()]/g, "\\$&")));

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText(markup, { selector: "p" })).toBeInTheDocument();
  });

  describe("feedback", () => {
    it("needs a reason before it sends anything", async () => {
      const posted = vi.fn();
      server.use(
        detailHandler(caseDetail()),
        stepsHandler,
        feedbackList,
        http.post("*/api/v1/cases/case-100/feedback", async ({ request }) => {
          posted(await request.json());
          return json({}, 201);
        }),
      );
      renderApp("/cases/case-100");
      await screen.findByText(/SMB connection accepted/);
      await userEvent.selectOptions(screen.getByLabelText("Doğru karar"), "fp");
      await userEvent.click(screen.getByRole("button", { name: "Geri bildirimi gönder" }));
      expect(await screen.findByText("Neden seçmelisiniz.")).toBeInTheDocument();
      expect(posted).not.toHaveBeenCalled();
    });

    it("rejects a comment over 500 characters", async () => {
      server.use(detailHandler(caseDetail()), stepsHandler, feedbackList);
      renderApp("/cases/case-100");
      await screen.findByText(/SMB connection accepted/);
      await userEvent.selectOptions(screen.getByLabelText("Doğru karar"), "fp");
      await userEvent.selectOptions(screen.getByLabelText("Neden"), "correct");
      await userEvent.click(screen.getByLabelText(/Yorum/));
      await userEvent.paste("x".repeat(501));
      await userEvent.click(screen.getByRole("button", { name: "Geri bildirimi gönder" }));
      expect(await screen.findByText("Yorum en çok 500 karakter olabilir.")).toBeInTheDocument();
    });

    it("sends the verdict, the reason and the comment", async () => {
      const posted = vi.fn();
      server.use(
        detailHandler(caseDetail()),
        stepsHandler,
        feedbackList,
        http.post("*/api/v1/cases/case-100/feedback", async ({ request }) => {
          posted(await request.json());
          return json({}, 201);
        }),
      );
      renderApp("/cases/case-100");
      await screen.findByText(/SMB connection accepted/);
      await userEvent.selectOptions(screen.getByLabelText("Doğru karar"), "fp");
      await userEvent.selectOptions(screen.getByLabelText("Neden"), "was_fp_not_tp");
      await userEvent.type(screen.getByLabelText(/Yorum/), "Bakım penceresi");
      await userEvent.click(screen.getByRole("button", { name: "Geri bildirimi gönder" }));
      expect(await screen.findByText("Geri bildirim kaydedildi.")).toBeInTheDocument();
      expect(posted).toHaveBeenCalledWith({
        case_id: "case-100",
        verdict: "fp",
        reason: "was_fp_not_tp",
        comment: "Bakım penceresi",
      });
    });

    it("shows the API's refusal in Turkish", async () => {
      server.use(
        detailHandler(caseDetail()),
        stepsHandler,
        feedbackList,
        http.post("*/api/v1/cases/case-100/feedback", () =>
          problemResponse(problem("request.invalid", 422), 422),
        ),
      );
      renderApp("/cases/case-100");
      await screen.findByText(/SMB connection accepted/);
      await userEvent.selectOptions(screen.getByLabelText("Doğru karar"), "tp");
      await userEvent.selectOptions(screen.getByLabelText("Neden"), "correct");
      await userEvent.click(screen.getByRole("button", { name: "Geri bildirimi gönder" }));
      expect(await screen.findByText("Girilen değerler geçersiz.")).toBeInTheDocument();
    });
  });
});
