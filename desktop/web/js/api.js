// 本地后端 HTTP 客户端。所有请求带 Bearer token（由桌面壳注入）。
let backend = { port: null, token: '' };

export function setBackend(info) {
  backend = { port: info.port, token: info.token || '' };
}

export function isReady() {
  return Boolean(backend.port);
}

export function endpoint(path) {
  return `http://127.0.0.1:${backend.port}${path}`;
}

async function request(path, { method = 'GET', body } = {}) {
  if (!backend.port) throw new Error('本地后端尚未就绪');
  const headers = { Authorization: `Bearer ${backend.token}` };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(endpoint(path), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data && data.error) message = data.error;
    } catch {
      /* 非 JSON 错误体，保留状态码 */
    }
    throw new Error(message);
  }
  return res;
}

export const api = {
  get: async (path) => (await request(path)).json(),
  post: async (path, body) => (await request(path, { method: 'POST', body: body ?? {} })).json(),
  put: async (path, body) => (await request(path, { method: 'PUT', body: body ?? {} })).json(),
  del: async (path) => (await request(path, { method: 'DELETE' })).json(),
  blob: async (path) => (await request(path)).blob(),
};

export function base64FromArrayBuffer(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunk = 0x8000;
  let binary = '';
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
