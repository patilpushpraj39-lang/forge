import { ApiAccessError } from "./api-client.ts";

export function githubCatalogError(error: unknown): string {
  if (error instanceof ApiAccessError) {
    if (error.status === 503) {
      return "GitHub connection is not configured or is temporarily unavailable. You can still use the free offline demo.";
    }
    if (error.status === 401 || error.status === 403) return error.message;
  }
  // Do not expose arbitrary server or network details in the UI.
  return "Unable to load GitHub repositories. Check the GitHub connection or use the free offline demo.";
}
