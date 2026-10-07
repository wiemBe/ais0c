import { tr } from "../i18n/tr";
import { ApiError, NetworkError } from "./client";

/** The Turkish message of a failed request. The API's `detail` is never shown; an unknown code
 * gets the generic message (T-029 criterion 3). */
export function errorMessage(error: unknown): string {
  if (error instanceof NetworkError) return tr.errors.network;
  if (error instanceof ApiError) {
    if (error.code !== null && error.code in tr.errors.codes) {
      return tr.errors.codes[error.code] ?? tr.errors.generic;
    }
    if (error.status === 403) return tr.errors.codes["auth.forbidden"] ?? tr.errors.generic;
    if (error.status === 503) return tr.errors.codes["storage.unavailable"] ?? tr.errors.generic;
  }
  return tr.errors.generic;
}
