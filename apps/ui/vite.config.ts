/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `/api` goes to the analyst API (services/api); it listens on 127.0.0.1:8000 by default.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: { modules: { classNameStrategy: "non-scoped" } },
  },
});
