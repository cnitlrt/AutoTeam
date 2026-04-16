import type {
  Account,
  AdminStatus,
  AutoCheckConfig,
  CodexAuthExport,
  LogsResponse,
  MainCodexStatus,
  ManualAccountStatus,
  ProxyConfig,
  SetupStatus,
  SMSProvider,
  SMSProvidersResponse,
  SMSRentalCreated,
  SMSService,
  StatusResponse,
  TaskItem,
  TeamMembersResponse,
} from "./types";

const BASE = "/api";
const API_KEY_STORAGE = "autoteam_api_key";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function getApiKey(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(API_KEY_STORAGE) || "";
}

export function setApiKey(key: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(API_KEY_STORAGE, key);
}

export function clearApiKey() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(API_KEY_STORAGE);
}

async function request<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const key = getApiKey();
  if (key) headers["Authorization"] = `Bearer ${key}`;
  const opts: RequestInit = { method, headers };
  if (body !== undefined && body !== null) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE}${path}`, opts);
  let data: unknown;
  const text = await resp.text();
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(`HTTP ${resp.status}: 服务器返回了非 JSON 响应`, resp.status);
  }
  if (!resp.ok) {
    const d = data as { detail?: { message?: string } | string } | null;
    const msg =
      (typeof d?.detail === "object" && d?.detail?.message) ||
      (typeof d?.detail === "string" && d.detail) ||
      `HTTP ${resp.status}`;
    throw new ApiError(String(msg), resp.status);
  }
  return data as T;
}

export const api = {
  checkAuth: () =>
    request<{ authenticated: boolean; auth_required: boolean }>("GET", "/auth/check"),
  getSetupStatus: () => request<SetupStatus>("GET", "/setup/status"),
  saveSetup: (config: Record<string, string>) =>
    request<{ message: string; api_key?: string; configured: boolean }>(
      "POST",
      "/setup/save",
      config,
    ),

  getStatus: () => request<StatusResponse>("GET", "/status"),
  getAdminStatus: () => request<AdminStatus>("GET", "/admin/status"),
  getMainCodexStatus: () => request<MainCodexStatus>("GET", "/main-codex/status"),
  getManualAccountStatus: () => request<ManualAccountStatus>("GET", "/manual-account/status"),

  getAccounts: () => request<Account[]>("GET", "/accounts"),
  getActiveAccounts: () => request<Account[]>("GET", "/accounts/active"),
  getStandbyAccounts: () => request<Account[]>("GET", "/accounts/standby"),
  deleteAccount: (email: string) =>
    request<{ message: string }>("DELETE", `/accounts/${encodeURIComponent(email)}`),
  loginAccount: (email: string) => request<TaskItem>("POST", "/accounts/login", { email }),
  getCodexAuth: (email: string) =>
    request<CodexAuthExport>("GET", `/accounts/${encodeURIComponent(email)}/codex-auth`),
  kickAccount: (email: string) =>
    request<{ message: string }>("POST", `/accounts/${encodeURIComponent(email)}/kick`),

  startAdminLogin: (email: string) =>
    request<{ status: string }>("POST", "/admin/login/start", { email }),
  submitAdminSession: (email: string, sessionToken: string) =>
    request<{ status: string }>("POST", "/admin/login/session", {
      email,
      session_token: sessionToken,
    }),
  submitAdminPassword: (password: string) =>
    request<{ status: string }>("POST", "/admin/login/password", { password }),
  submitAdminCode: (code: string) =>
    request<{ status: string }>("POST", "/admin/login/code", { code }),
  submitAdminWorkspace: (optionId: string) =>
    request<{ status: string }>("POST", "/admin/login/workspace", { option_id: optionId }),
  cancelAdminLogin: () => request<{ message: string }>("POST", "/admin/login/cancel"),
  logoutAdmin: () => request<{ message: string }>("POST", "/admin/logout"),

  startMainCodexSync: () => request<{ status: string }>("POST", "/main-codex/start"),
  submitMainCodexPassword: (password: string) =>
    request<{ status: string }>("POST", "/main-codex/password", { password }),
  submitMainCodexCode: (code: string) =>
    request<{ status: string }>("POST", "/main-codex/code", { code }),
  cancelMainCodexSync: () => request<{ message: string }>("POST", "/main-codex/cancel"),

  startManualAccount: () =>
    request<{ manual_account: ManualAccountStatus; auth_url: string }>(
      "POST",
      "/manual-account/start",
    ),
  submitManualAccountCallback: (redirectUrl: string) =>
    request<{ status: string }>("POST", "/manual-account/callback", { redirect_url: redirectUrl }),
  cancelManualAccount: () => request<{ message: string }>("POST", "/manual-account/cancel"),

  postSync: () => request<{ message: string }>("POST", "/sync"),
  postSyncFromCpa: () => request<{ message: string }>("POST", "/sync/from-cpa"),
  postSyncAccounts: () => request<{ message: string; total?: number }>("POST", "/sync/accounts"),
  postSyncMainCodex: () => request<{ status: string }>("POST", "/sync/main-codex"),

  startRotate: (target = 5) => request<TaskItem>("POST", "/tasks/rotate", { target }),
  startCheck: () => request<TaskItem>("POST", "/tasks/check"),
  startAdd: () => request<TaskItem>("POST", "/tasks/add"),
  startFill: (target = 5) => request<TaskItem>("POST", "/tasks/fill", { target }),
  startCleanup: (maxSeats: number | null = null) =>
    request<TaskItem>("POST", "/tasks/cleanup", { max_seats: maxSeats }),

  getTasks: () => request<TaskItem[]>("GET", "/tasks"),
  getTask: (id: string) => request<TaskItem>("GET", `/tasks/${id}`),
  cancelTask: (id: string) =>
    request<{ message: string; task: TaskItem }>(
      "POST",
      `/tasks/${encodeURIComponent(id)}/cancel`,
    ),

  getAutoCheckConfig: () => request<AutoCheckConfig>("GET", "/config/auto-check"),
  setAutoCheckConfig: (cfg: AutoCheckConfig) =>
    request<AutoCheckConfig>("PUT", "/config/auto-check", cfg),

  getTeamMembers: () => request<TeamMembersResponse>("GET", "/team/members"),
  removeTeamMember: (payload: { email: string; user_id: string; type: "member" | "invite" }) =>
    request<{ message: string }>("POST", "/team/members/remove", payload),

  getLogs: (limit = 200, since = 0) =>
    request<LogsResponse>("GET", `/logs?limit=${limit}&since=${since}`),

  getBrowserStatus: () =>
    request<{
      active: boolean;
      label?: string;
      email?: string;
      member_type?: string;
      started_at?: number;
      elapsed_seconds?: number;
    }>("GET", "/browser/status"),

  getSmsProviders: () => request<SMSProvidersResponse>("GET", "/sms/providers"),
  addSmsProvider: (params: {
    type: string;
    api_key: string;
    label?: string;
    enabled?: boolean;
  }) => request<SMSProvider>("POST", "/sms/providers", params),
  updateSmsProvider: (
    id: string,
    params: { api_key?: string; label?: string; enabled?: boolean },
  ) => request<SMSProvider>("PUT", `/sms/providers/${encodeURIComponent(id)}`, params),
  deleteSmsProvider: (id: string) =>
    request<{ message: string }>("DELETE", `/sms/providers/${encodeURIComponent(id)}`),
  reorderSmsProviders: (order: string[]) =>
    request<{ providers: SMSProvider[] }>("POST", "/sms/providers/reorder", { order }),
  testSmsProvider: (id: string) =>
    request<{ balance: number }>("POST", `/sms/providers/${encodeURIComponent(id)}/test`),
  getSmsProviderServices: (id: string) =>
    request<{ services: SMSService[] }>("GET", `/sms/providers/${encodeURIComponent(id)}/services`),
  rentSms: (params: {
    service?: string;
    provider_id?: string;
    max_price?: number | null;
    carrier?: string | null;
    keep_carrier?: boolean | null;
    lock_area_code?: boolean | null;
    area_codes?: string | null;
  }) => request<SMSRentalCreated>("POST", "/sms/rent", params),
  cancelSms: (id: string, providerId: string) =>
    request<{ message: string }>(
      "POST",
      `/sms/rentals/${encodeURIComponent(id)}/cancel?provider_id=${encodeURIComponent(providerId)}`,
    ),
  completeSms: (id: string, providerId: string) =>
    request<{ message: string }>(
      "POST",
      `/sms/rentals/${encodeURIComponent(id)}/complete?provider_id=${encodeURIComponent(providerId)}`,
    ),
  getSmsRentalStatus: (id: string, providerId: string) =>
    request<{ id: number; status: string; code: string | null; number: string }>(
      "GET",
      `/sms/rentals/${encodeURIComponent(id)}?provider_id=${encodeURIComponent(providerId)}`,
    ),

  getProxyConfig: () => request<ProxyConfig>("GET", "/proxy/config"),
  setProxyConfig: (params: { enabled?: boolean; check_interval?: number }) =>
    request<{ enabled: boolean; check_interval: number }>("PUT", "/proxy/config", params),
  addProxies: (proxies: string) =>
    request<{ added: number; skipped: number; total: number }>("POST", "/proxy/add", { proxies }),
  deleteProxy: (id: string) =>
    request<{ message: string }>("DELETE", `/proxy/${encodeURIComponent(id)}`),
  deleteAllProxies: () => request<{ message: string; deleted: number }>("POST", "/proxy/delete-all"),
  checkProxies: () => request<{ message: string }>("POST", "/proxy/check"),
};

export type Api = typeof api;
