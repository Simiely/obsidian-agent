// 后端 API 封装
export async function api(path, options = {}) {
  const init = { headers: { "Content-Type": "application/json" }, ...options };
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
}

export const apiGet = (p) => api(p);
export const apiPut = (p, b) => api(p, { method: "PUT", body: JSON.stringify(b) });
export const apiPost = (p, b) => api(p, { method: "POST", body: JSON.stringify(b) });
export const apiDelete = (p) => api(p, { method: "DELETE" });
