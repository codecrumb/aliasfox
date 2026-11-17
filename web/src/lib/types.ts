export interface Alias {
  anonymous_id: string;
  hme: string;
  label: string | null;
  note: string | null;
  is_active: boolean;
  forward_to: string | null;
  origin: string | null;
  domain: string | null;
  created_ms: number | null;
  origin_app_name: string | null;
  app_bundle_id: string | null;
}

export interface SessionInfo {
  cache_key: string;
  mode: string;
  auth_enabled: boolean;
}

export interface StatusInfo {
  connected: boolean;
  mode: string;
  auth_enabled: boolean;
  reason?: string;
}

export type ConnectLoginResult = { status: 'ok' } | { status: 'needs_2fa'; flow_id: string };

export type SessionState = 'loading' | 'login' | 'connect' | 'ready' | 'reconnect' | 'error';
