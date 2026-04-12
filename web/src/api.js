const BASE = '/api'

async function request(method, path, body = null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }
  if (body) opts.body = JSON.stringify(body)
  const resp = await fetch(`${BASE}${path}`, opts)
  const data = await resp.json()
  if (!resp.ok) {
    const msg = data?.detail?.message || data?.detail || `HTTP ${resp.status}`
    throw new Error(msg)
  }
  return data
}

export const api = {
  getStatus: () => request('GET', '/status'),
  getAccounts: () => request('GET', '/accounts'),
  getActiveAccounts: () => request('GET', '/accounts/active'),
  getStandbyAccounts: () => request('GET', '/accounts/standby'),
  deleteAccount: (email) => request('DELETE', `/accounts/${encodeURIComponent(email)}`),
  getCpaFiles: () => request('GET', '/cpa/files'),

  postSync: () => request('POST', '/sync'),

  startRotate: (target = 5) => request('POST', '/tasks/rotate', { target }),
  startCheck: () => request('POST', '/tasks/check'),
  startAdd: () => request('POST', '/tasks/add'),
  startFill: (target = 5) => request('POST', '/tasks/fill', { target }),
  startCleanup: (maxSeats = null) => request('POST', '/tasks/cleanup', { max_seats: maxSeats }),

  getTasks: () => request('GET', '/tasks'),
  getTask: (id) => request('GET', `/tasks/${id}`),

  getAutoCheckConfig: () => request('GET', '/config/auto-check'),
  setAutoCheckConfig: (cfg) => request('PUT', '/config/auto-check', cfg),
}
