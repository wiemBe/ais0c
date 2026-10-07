import type { ApiProblem } from "./types";

const TOKEN_KEY = "ais0c.token";

/** The dev token the user pasted; it lives in `sessionStorage` only (T-029 criterion 2). */
export function readToken(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function storeToken(token: string | null): void {
  try {
    if (token === null) window.sessionStorage.removeItem(TOKEN_KEY);
    else window.sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    // A browser without session storage keeps the token in memory only (AuthProvider).
  }
}

/** An answer of the API that is not a 2xx. `code` is the problem's `title`; `detail` is never
 * shown to the user (T-029 criterion 3). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly problem: ApiProblem | null;

  constructor(status: number, problem: ApiProblem | null) {
    super(problem?.title ?? `http ${status}`);
    this.status = status;
    this.code = problem?.title ?? null;
    this.problem = problem;
  }
}

/** The request never reached the API (offline, refused connection). */
export class NetworkError extends Error {}

export type QueryValue =
  string | number | boolean | null | undefined | readonly (string | number)[];

export function buildQuery(query: Record<string, QueryValue>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
    } else {
      params.append(key, String(value));
    }
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

type Options = { query?: Record<string, QueryValue>; body?: unknown };

const API_PREFIX = "/api/v1";

export async function request<T>(method: string, path: string, options: Options = {}): Promise<T> {
  const token = readToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const url = new URL(
    `${API_PREFIX}${path}${buildQuery(options.query ?? {})}`,
    window.location.origin,
  );
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new NetworkError("network");
  }
  if (!response.ok) {
    throw new ApiError(response.status, await readProblem(response));
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function readProblem(response: Response): Promise<ApiProblem | null> {
  try {
    return (await response.json()) as ApiProblem;
  } catch {
    return null;
  }
}

export const get = <T>(path: string, query?: Record<string, QueryValue>) =>
  request<T>("GET", path, { query });
export const post = <T>(path: string, body?: unknown) => request<T>("POST", path, { body });
export const put = <T>(path: string, body?: unknown) => request<T>("PUT", path, { body });
export const del = <T>(path: string) => request<T>("DELETE", path);
