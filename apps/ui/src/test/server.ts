import { setupServer } from "msw/node";

// The API is always a stand-in: no test reaches a real server (T-029 "Kabul kriterleri").
export const server = setupServer();
