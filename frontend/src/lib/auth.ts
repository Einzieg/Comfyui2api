const TOKEN_KEY = "comfyui2api.adminToken";

export function getAdminToken(): string {
  return window.localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setAdminToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearAdminToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}
