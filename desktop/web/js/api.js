// 本地后端 HTTP 客户端。所有请求带 Bearer token（由桌面壳注入）。
//
// 两种后端地址：
//   - 桌面壳 / 浏览器调试：127.0.0.1:<随机端口>（token 必须带）
//   - Web 部署（Docker）：同源，直接用相对路径 /api/**（后端可能没配 token）
let backend = { port: null, token: '', sameOrigin: false };

export function setBackend(info) {
  const port = info && info.port ? Number(info.port) : null;
  backend = { port, token: (info && info.token) || '', sameOrigin: !port };
}

export function isReady() {
  return Boolean(backend.port) || backend.sameOrigin;
}

export function endpoint(path) {
  return backend.sameOrigin ? path : `http://127.0.0.1:${backend.port}${path}`;
}

async function request(path, { method = 'GET', body } = {}) {
  if (!isReady()) throw new Error('本地后端尚未就绪');
  const headers = {};
  if (backend.token) headers.Authorization = `Bearer ${backend.token}`;
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

/**
 * 带上传进度的 POST。fetch 拿不到请求体进度，导入大 TXT 时只能干等，
 * 所以这里退回 XHR：``onProgress`` 收到 0~1 的上传比例（服务端开始解析后不再回调）。
 */
function requestWithProgress(path, body, onProgress) {
  if (!isReady()) return Promise.reject(new Error('本地后端尚未就绪'));
  const payload = JSON.stringify(body ?? {});
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', endpoint(path));
    if (backend.token) xhr.setRequestHeader('Authorization', `Bearer ${backend.token}`);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.upload.onprogress = (event) => {
      if (typeof onProgress !== 'function') return;
      onProgress(event.lengthComputable && event.total ? event.loaded / event.total : 0);
    };
    xhr.onload = () => {
      let data = null;
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : {};
      } catch {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data ?? {});
        return;
      }
      reject(new Error((data && data.error) || `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error('网络中断（上传失败）'));
    xhr.onabort = () => reject(new Error('已取消'));
    xhr.send(payload);
  });
}

export const api = {
  get: async (path) => (await request(path)).json(),
  post: async (path, body) => (await request(path, { method: 'POST', body: body ?? {} })).json(),
  postWithProgress: (path, body, onProgress) => requestWithProgress(path, body, onProgress),
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
