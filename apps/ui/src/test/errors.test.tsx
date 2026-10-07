import { screen } from "@testing-library/react";
import { http } from "msw";
import { describe, expect, it } from "vitest";
import { ApiError, NetworkError } from "../api/client";
import { errorMessage } from "../api/errors";
import { problem } from "./fixtures";
import { problemResponse, renderApp } from "./render";
import { server } from "./server";

describe("errors (criterion 3)", () => {
  it("translates a known problem code and never shows the detail", async () => {
    server.use(
      http.get("*/api/v1/cases/case-9", () => problemResponse(problem("case.not_found", 404), 404)),
    );
    renderApp("/cases/case-9");
    expect(await screen.findByText("Vaka bulunamadı.")).toBeInTheDocument();
    expect(screen.queryByText(/DETAIL-THE-USER-MUST-NOT-SEE/)).not.toBeInTheDocument();
  });

  it("shows a generic message for an unknown code", async () => {
    server.use(
      http.get("*/api/v1/cases/case-9", () =>
        problemResponse(problem("something.new_and_unknown", 418), 418),
      ),
    );
    renderApp("/cases/case-9");
    expect(await screen.findByText("Bir hata oluştu. Lütfen tekrar deneyin.")).toBeInTheDocument();
    expect(screen.queryByText(/something.new_and_unknown/)).not.toBeInTheDocument();
    expect(screen.queryByText(/DETAIL-THE-USER-MUST-NOT-SEE/)).not.toBeInTheDocument();
  });

  it("explains a 503", async () => {
    server.use(
      http.get("*/api/v1/cases", () => problemResponse(problem("storage.unavailable", 503), 503)),
    );
    renderApp("/");
    expect(await screen.findByText(/Veritabanına şu an ulaşılamıyor/)).toBeInTheDocument();
  });

  it("falls back on the status when the body is not a problem", () => {
    expect(errorMessage(new ApiError(503, null))).toMatch(/Veritabanına/);
    expect(errorMessage(new ApiError(403, null))).toBe("Bu işlem için yetkiniz yok.");
    expect(errorMessage(new ApiError(500, null))).toBe("Bir hata oluştu. Lütfen tekrar deneyin.");
    expect(errorMessage(new NetworkError("x"))).toBe("API'ye ulaşılamıyor.");
    expect(errorMessage(new Error("boom"))).toBe("Bir hata oluştu. Lütfen tekrar deneyin.");
  });
});
