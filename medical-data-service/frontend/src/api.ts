const API_BASE = import.meta.env.VITE_API_BASE ?? ''
const TOKEN_KEY = 'medical_id_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t)
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getToken()
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...options,
  })
  if (res.status === 401) {
    clearToken()
    throw new Error('unauthorized')
  }
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<T>
}

// -- public config + Cognito hosted-UI (Authorization Code + PKCE) -----------

export type PublicConfig = {
  cognito_domain: string | null
  cognito_client_id: string | null
  mcp_auth_mode: 'oauth' | 'api_key'
  mcp_agent_client_id: string | null
  mcp_resource_url: string | null
}

export const fetchConfig = () =>
  fetch(`${API_BASE}/api/config`).then((r) => r.json() as Promise<PublicConfig>)

const REDIRECT_URI = () => `${window.location.origin}/callback`
const VERIFIER_KEY = 'medical_pkce_verifier'

function base64Url(bytes: Uint8Array): string {
  let s = ''
  bytes.forEach((b) => (s += String.fromCharCode(b)))
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
function randomVerifier(): string {
  const bytes = new Uint8Array(32)
  crypto.getRandomValues(bytes)
  return base64Url(bytes)
}
async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return base64Url(new Uint8Array(digest))
}

export async function beginLogin(cfg: PublicConfig): Promise<void> {
  const verifier = randomVerifier()
  sessionStorage.setItem(VERIFIER_KEY, verifier)
  const challenge = await challengeFor(verifier)
  const params = new URLSearchParams({
    client_id: cfg.cognito_client_id ?? '',
    response_type: 'code',
    scope: 'openid email profile',
    redirect_uri: REDIRECT_URI(),
    code_challenge_method: 'S256',
    code_challenge: challenge,
  })
  window.location.assign(`https://${cfg.cognito_domain}/oauth2/authorize?${params}`)
}

export async function completeLogin(cfg: PublicConfig, code: string): Promise<string> {
  const verifier = sessionStorage.getItem(VERIFIER_KEY)
  if (!verifier) throw new Error('missing PKCE verifier; restart login')
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: cfg.cognito_client_id ?? '',
    code,
    redirect_uri: REDIRECT_URI(),
    code_verifier: verifier,
  })
  const res = await fetch(`https://${cfg.cognito_domain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  if (!res.ok) throw new Error(await res.text())
  const tokens = (await res.json()) as { id_token: string }
  sessionStorage.removeItem(VERIFIER_KEY)
  return tokens.id_token
}

export function logout(cfg: PublicConfig): void {
  clearToken()
  const params = new URLSearchParams({
    client_id: cfg.cognito_client_id ?? '',
    logout_uri: `${window.location.origin}/`,
  })
  window.location.assign(`https://${cfg.cognito_domain}/logout?${params}`)
}

// -- decode id_token to show the current user's email ------------------------

export function currentEmail(): string | null {
  const t = getToken()
  if (!t) return null
  try {
    const payload = JSON.parse(atob(t.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    return payload.email ?? null
  } catch {
    return null
  }
}

// -- authenticated API -------------------------------------------------------

export type ServerInfo = {
  name: string
  is_admin: boolean
  mcp_path: string
  transport: string
  execution_enabled: boolean
  tools: { name: string; description: string }[]
}
export type CatalogItem = { name: string; description: string }
export type QueryResult = {
  metrics: string[]
  group_by: string[]
  sql: string
  columns?: string[]
  rows?: Record<string, unknown>[]
  row_count?: number
}
export type ApiKey = {
  id: string
  name: string
  masked_key: string
  created_at: string
  created_by: string
  revoked_at: string | null
  active: boolean
}

export const getInfo = () => request<ServerInfo>('/api/info')
export const getCatalog = () =>
  request<{ metrics: CatalogItem[]; dimensions: CatalogItem[] }>('/api/metrics')
export const previewSql = (metrics: string[], group_by: string[]) =>
  request<QueryResult>('/api/query/preview', {
    method: 'POST',
    body: JSON.stringify({ metrics, group_by }),
  })
export const runQuery = (metrics: string[], group_by: string[], limit = 100) =>
  request<QueryResult>('/api/query', {
    method: 'POST',
    body: JSON.stringify({ metrics, group_by, limit }),
  })

export const listKeys = () => request<ApiKey[]>('/api/admin/keys')
export const createKey = (name: string) =>
  request<ApiKey & { api_key: string }>('/api/admin/keys', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
export const revokeKey = (id: string) =>
  request<{ revoked: string }>(`/api/admin/keys/${id}`, { method: 'DELETE' })
