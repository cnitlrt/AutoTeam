export type AccountStatus = "active" | "standby" | "exhausted" | "pending";

export interface QuotaInfo {
  primary_pct?: number;
  weekly_pct?: number;
  primary_resets_at?: number;
  weekly_resets_at?: number;
}

export interface Account {
  email: string;
  status: AccountStatus;
  auth_file?: string | null;
  last_quota?: QuotaInfo | null;
  last_active_at?: number | null;
  created_at?: number;
  updated_at?: number;
}

export interface StatusResponse {
  accounts: Account[];
  summary: {
    active: number;
    standby: number;
    exhausted: number;
    pending: number;
    total: number;
  };
  quota_cache: Record<string, QuotaInfo>;
}

export interface TaskItem {
  task_id: string;
  command: string;
  params: Record<string, unknown>;
  status: "pending" | "running" | "completed" | "failed";
  created_at: number;
  started_at?: number | null;
  finished_at?: number | null;
  result?: unknown;
  error?: string | null;
}

export interface TeamMember {
  email: string;
  role: string;
  user_id: string;
  is_local: boolean;
  type: "member" | "invite";
}

export interface TeamMembersResponse {
  members: TeamMember[];
  total: number;
  invites: number;
}

export interface LogLine {
  time: number;
  level: string;
  message: string;
}

export interface LogsResponse {
  logs: LogLine[];
  total: number;
}

export interface AdminStatus {
  configured: boolean;
  email?: string;
  account_id?: string;
  workspace_name?: string;
  workspace_options?: Array<{ id: string; label: string }>;
  login_step?: "password_required" | "code_required" | "workspace_required" | null;
  login_in_progress?: boolean;
  session_present?: boolean;
  password_saved?: boolean;
}

export interface MainCodexStatus {
  in_progress: boolean;
  step?: "password_required" | "code_required" | null;
}

export interface ManualAccountStatus {
  in_progress: boolean;
  status: "idle" | "waiting" | "completed" | "error" | string;
  state?: string;
  auth_url?: string;
  started_at?: number;
  message?: string;
  error?: string;
  account?: Account | null;
  callback_received?: boolean;
  callback_source?: "auto" | "manual" | "";
  auto_callback_available?: boolean;
  auto_callback_error?: string;
}

export interface SetupField {
  key: string;
  prompt: string;
  default?: string;
  optional?: boolean;
  configured?: boolean;
}

export interface SetupStatus {
  configured: boolean;
  fields: SetupField[];
}

export interface AutoCheckConfig {
  interval: number;
  threshold: number;
  min_low: number;
}

export interface SMSProvider {
  id: string;
  type: string;
  label: string;
  enabled: boolean;
  has_key: boolean;
  balance?: number | null;
  error?: string | null;
}

export interface SMSProviderType {
  type: string;
  name: string;
  api_key_label: string;
  help?: string;
}

export interface SMSProvidersResponse {
  providers: SMSProvider[];
  available_types: SMSProviderType[];
  default_service: string;
}

export interface SMSService {
  service_name?: string;
  api_name?: string;
  price?: string | number;
  stock?: number;
  ttl?: number;
  multiple_sms?: string | boolean;
}

export interface SMSRentalCreated {
  id: string;
  number: string;
  service: string;
  price: number;
  provider_id: string;
  provider_type: string;
}

export interface CodexAuthExport {
  email: string;
  codex_auth: {
    auth_mode?: string;
    OPENAI_API_KEY?: string | null;
    tokens?: {
      id_token?: string;
      access_token?: string;
      refresh_token?: string;
      account_id?: string;
    };
    last_refresh?: string;
  };
  hint?: string;
}

export type ProxyStatus = "good" | "slow" | "bad" | "unchecked";

export interface ProxyEntry {
  id: string;
  host: string;
  port: number;
  username: string;
  password: string;
  latency_ms: number | null;
  status: ProxyStatus;
  last_check: number | null;
}

export interface ProxyConfig {
  enabled: boolean;
  check_interval: number;
  proxies: ProxyEntry[];
}
