import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { ApiError } from "./client";

let onUnauthorized: (() => void) | null = null;

/** The session provider registers what a 401 does: end the session and show the login. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

function check(error: unknown): void {
  if (error instanceof ApiError && error.status === 401) onUnauthorized?.();
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({ onError: check }),
    mutationCache: new MutationCache({ onError: check }),
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
}
