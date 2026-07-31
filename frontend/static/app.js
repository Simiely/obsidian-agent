/* Obsidian Agent 前端应用（零构建静态方案，M4；M6 可迁移 Vue3）
 * 功能：文件树 / 阅读 / 编辑 / 全文搜索 / 索引与备份状态
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = { treeLoaded: false, currentPath: null, content: null, mode: "read" };

  // ---------- API 封装 ----------
  async function api(path, options = {}) {
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
  const apiGet = (p) => api(p);
  const apiPut = (p, b) => api(p, { method: "PUT", body: JSON.stringify(b) });
  const apiPost = (p, b) => api(p, { method: "POST", body: JSON.stringify(b) });
  const apiDelete = (p) => api(p, { method: "DELETE" });

  // ---------- Toast ----------
  function toast(msg, isError = false) {
    const el = $("toast");
    el.textContent = msg;
    el.className = isError ? "toast error" : "toast";
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (el.hidden = true), 3000);
  }

  // ---------- 文件树 ----------
  function renderTree(nodes, container) {
    container.innerHTML = "";
    for (const n of nodes) {
      const div = document.createElement("div");
      div.className = "tree-node";
      if (n.type === "dir") {
        div.innerHTML = `<span class="twisty">▸</span><span class="tree-name">${MdRender.escapeHtml(n.name)}</span>`;
        div.dataset.type = "dir";
        div.dataset.path = n.path;
        const kids = document.createElement("div");
        kids.className = "tree-children";
        kids.hidden = true;
        div.appendChild(kids);
        div.addEventListener("click", async (e) => {
          if (e.target.closest(".tree-name, .twisty")) {
            const expand = kids.hidden;
            kids.hidden = !expand;
            div.querySelector(".twisty").textContent = expand ? "▾" : "▸";
            if (expand && !kids.dataset.loaded) {
              try {
                const sub = await apiGet(`/api/vault/tree?path=${encodeURIComponent(n.path)}`);
                renderTree(sub, kids);
                kids.dataset.loaded = "1";
              } catch (err) { toast(err.message, true); }
            }
          }
        });
      } else {
        div.className += " tree-file";
        div.innerHTML = `<span class="tree-name">${MdRender.escapeHtml(n.name)}</span>`;
        div.dataset.type = "file";
        div.dataset.path = n.path;
        div.addEventListener("click", () => openFile(n.path));
      }
      container.appendChild(div);
    }
  }

  async function loadTree() {
    try {
      const nodes = await apiGet("/api/vault/tree");
      renderTree(nodes, $("tree"));
      state.treeLoaded = true;
    } catch (err) {
      toast("加载目录失败：" + err.message, true);
    }
  }

  // ---------- 打开 / 阅读 / 编辑 ----------
  function setMode(mode) {
    state.mode = mode;
    $("doc-view").hidden = mode !== "read";
    $("doc-editor").hidden = mode !== "edit";
    $("btn-edit").hidden = mode !== "read";
    $("btn-save").hidden = mode !== "edit";
    $("btn-cancel").hidden = mode !== "edit";
  }

  async function openFile(path, highlightQuery) {
    try {
      const data = await apiGet(`/api/vault/file?path=${encodeURIComponent(path)}`);
      state.currentPath = path;
      state.content = data.content;
      $("doc-header").hidden = false;
      $("welcome").hidden = true;
      $("search-results").hidden = true;
      $("doc-path").textContent = path;
      $("doc-path").title = path;
      $("doc-view").innerHTML = MdRender.render(data.content);
      document.querySelectorAll("#tree .tree-file.selected").forEach((el) => el.classList.remove("selected"));
      const sel = document.querySelector(`#tree .tree-file[data-path="${CSS.escape(path)}"]`);
      if (sel) sel.classList.add("selected");
      if (highlightQuery) highlightInDoc(highlightQuery);
      setMode("read");
    } catch (err) {
      toast("打开失败：" + err.message, true);
    }
  }

  function highlightInDoc(query) {
    const root = $("doc-view");
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const targets = [];
    let node;
    while ((node = walker.nextNode())) {
      if (node.textContent.includes(query)) targets.push(node);
    }
    if (!targets.length) return;
    const first = targets[0];
    const span = document.createElement("mark");
    const parent = first.parentNode;
    const idx = first.textContent.indexOf(query);
    const after = document.createElement("span");
    first.splitText(idx);
    after.textContent = first.textContent.slice(query.length);
    first.textContent = query;
    span.textContent = query;
    parent.replaceChild(after, first);
    parent.insertBefore(span, after);
    span.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  function startEdit() {
    if (!state.currentPath) return;
    $("doc-editor").value = state.content;
    setMode("edit");
    $("doc-editor").focus();
  }

  async function saveDoc() {
    if (!state.currentPath) return;
    try {
      await apiPut("/api/vault/file", { path: state.currentPath, content: $("doc-editor").value });
      toast("已保存 ✓");
      await openFile(state.currentPath);
    } catch (err) {
      toast("保存失败：" + err.message, true);
    }
  }

  // ---------- 搜索 ----------
  async function runSearch(q) {
    try {
      const data = await apiGet(`/api/search?q=${encodeURIComponent(q)}&page=1&pageSize=20`);
      const box = $("search-results");
      $("welcome").hidden = true;
      $("doc-header").hidden = true;
      $("doc-view").hidden = true;
      box.hidden = false;
      if (!data.total) {
        box.innerHTML = `<div class="empty">未找到与「${MdRender.escapeHtml(q)}」相关的结果</div>`;
        return;
      }
      let html = `<div class="search-meta">共 ${data.total} 条结果（${q}）</div>`;
      for (const r of data.results) {
        const snip = (r.snippets && r.snippets[0] && r.snippets[0].text) || "";
        html += `
          <div class="search-hit" data-path="${MdRender.escapeHtml(r.path)}">
            <div class="hit-title">${MdRender.escapeHtml(r.title)}</div>
            <div class="hit-path">${MdRender.escapeHtml(r.path)}</div>
            <div class="hit-snippet">${snip.replace(/</g, "&lt;")}</div>
            ${(r.tags || []).map((t) => `<span class="tag">#${MdRender.escapeHtml(t)}</span>`).join("")}
          </div>`;
      }
      box.innerHTML = html;
      box.querySelectorAll(".search-hit").forEach((el) =>
        el.addEventListener("click", () => openFile(el.dataset.path, q))
      );
    } catch (err) {
      toast("搜索失败：" + err.message, true);
    }
  }

  // ---------- 状态轮询 ----------
  async function refreshStatus() {
    try {
      const st = await apiGet("/api/index/status");
      $("index-status").textContent = `索引：${st.state === "ready" ? `就绪 (${st.totalFiles})` : "构建中…"}`;
      $("index-dot").className = "dot " + (st.state === "ready" ? "ok" : "busy");
    } catch (_) { /* 服务未就绪时忽略 */ }
    try {
      const bk = await apiGet("/api/backup/list");
      const runner = bk.runner || {};
      $("backup-status").textContent =
        runner.running ? `备份：进行中…` : `备份：${bk.snapshots.length} 份快照`;
      $("backup-dot").className = "dot " + (runner.running ? "busy" : "ok");
    } catch (_) { /* ignore */ }
  }

  // ---------- 事件绑定 ----------
  function bind() {
    $("search-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && $("search-input").value.trim()) {
        runSearch($("search-input").value.trim());
      }
    });
    $("btn-edit").addEventListener("click", startEdit);
    $("btn-save").addEventListener("click", saveDoc);
    $("btn-cancel").addEventListener("click", () => { if (state.currentPath) openFile(state.currentPath); });
    $("backup-now").addEventListener("click", async () => {
      try {
        await apiPost("/api/backup/now");
        toast("备份任务已启动");
        setTimeout(refreshStatus, 500);
      } catch (err) { toast("备份失败：" + err.message, true); }
    });
    // wikilink 点击打开目标文件
    $("doc-view").addEventListener("click", (e) => {
      const link = e.target.closest("a.wikilink");
      if (link && link.dataset.path) openFile(link.dataset.path);
    });
    // 编辑区 Ctrl/Cmd+S 保存
    $("doc-editor").addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        saveDoc();
      }
    });
  }

  // ---------- 启动 ----------
  async function init() {
    bind();
    await loadTree();
    refreshStatus();
    setInterval(refreshStatus, 5000);
    try {
      const h = await apiGet("/api/health");
      $("app-version").textContent = "v" + h.version;
    } catch (_) { /* ignore */ }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
