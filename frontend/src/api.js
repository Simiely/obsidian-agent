// 后端 API 封装（带超时——防止请求挂起导致前端状态锁死）
const DEFAULT_TIMEOUT = 8000; // 8s：正常接口 <1s，超时视为后端异常

export async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT);
  const init = {
    headers: { "Content-Type": "application/json" },
    signal: controller.signal,
    ...options,
  };
  try {
    const res = await fetch(path, init);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        if (body && body.detail) detail = body.detail;
      } catch (_) { /* ignore */ }
      throw new Error(`${res.status}: ${detail}`);
    }
    return res.status === 204 ? null : res.json();
  } finally {
    clearTimeout(timer);
  }
}

export const apiGet = (p) => api(p);
export const apiPut = (p, b) => api(p, { method: "PUT", body: JSON.stringify(b) });
export const apiPost = (p, b) => api(p, { method: "POST", body: JSON.stringify(b) });
export const apiDelete = (p) => api(p, { method: "DELETE" });
