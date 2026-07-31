<script setup>
// 应用主布局：搜索栏 + 文件树 + 文档视图 / 搜索结果 / Agent 对话
import { ref, onMounted, onUnmounted } from "vue";
import FileTree from "./components/FileTree.vue";
import DocView from "./components/DocView.vue";
import SearchPanel from "./components/SearchPanel.vue";
import ChatPanel from "./components/ChatPanel.vue";
import { apiGet, apiPut, apiPost } from "./api.js";

const version = ref("");
const treeNodes = ref([]);
const currentPath = ref("");
const docContent = ref("");
const docMeta = ref(null);
const highlightQuery = ref("");
const viewMode = ref("doc"); // doc | search | chat
const searchResults = ref([]);
const searchTotal = ref(0);
const searchQuery = ref("");
const indexStatus = ref("索引：-");
const indexOk = ref(false);
const backupText = ref("备份：-");
const backupBusy = ref(false);

let timer = null;
let toastTimer = null;

function toast(msg, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = isError ? "toast error" : "toast";
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 3000);
}

async function loadTree() {
  try {
    treeNodes.value = await apiGet("/api/vault/tree");
  } catch (e) {
    toast("加载目录失败：" + e.message, true);
  }
}

async function loadDir(path) {
  try {
    const nodes = await apiGet(`/api/vault/tree?path=${encodeURIComponent(path)}`);
    const dir = findNode(treeNodes.value, path);
    if (dir) dir.children = nodes;
  } catch (e) {
    toast("加载子目录失败：" + e.message, true);
  }
}

function findNode(nodes, path) {
  for (const n of nodes) {
    if (n.path === path) return n;
    if (n.children) {
      const hit = findNode(n.children, path);
      if (hit) return hit;
    }
  }
  return null;
}

async function openFile(path, query = "") {
  try {
    const data = await apiGet(`/api/vault/file?path=${encodeURIComponent(path)}`);
    currentPath.value = path;
    docContent.value = data.content;
    docMeta.value = data.meta;
    highlightQuery.value = query;
    viewMode.value = "doc";
  } catch (e) {
    toast("打开失败：" + e.message, true);
  }
}

async function saveDoc(content) {
  try {
    await apiPut("/api/vault/file", { path: currentPath.value, content });
    toast("已保存 ✓");
    await openFile(currentPath.value);
  } catch (e) {
    toast("保存失败：" + e.message, true);
  }
}

async function runSearch() {
  const q = searchQuery.value.trim();
  if (!q) return;
  try {
    const data = await apiGet(`/api/search?q=${encodeURIComponent(q)}&page=1&pageSize=20`);
    searchResults.value = data.results;
    searchTotal.value = data.total;
    viewMode.value = "search";
  } catch (e) {
    toast("搜索失败：" + e.message, true);
  }
}

async function refreshStatus() {
  try {
    const st = await apiGet("/api/index/status");
    indexStatus.value = st.state === "ready" ? `索引：就绪 (${st.totalFiles})` : "索引：构建中…";
    indexOk.value = st.state === "ready";
  } catch (_) { /* ignore */ }
  try {
    const bk = await apiGet("/api/backup/list");
    const runner = bk.runner || {};
    backupBusy.value = !!runner.running;
    backupText.value = runner.running ? "备份：进行中…" : `备份：${bk.snapshots.length} 份快照`;
  } catch (_) { /* ignore */ }
}

async function backupNow() {
  try {
    await apiPost("/api/backup/now");
    toast("备份任务已启动");
    setTimeout(refreshStatus, 500);
  } catch (e) {
    toast("备份失败：" + e.message, true);
  }
}

onMounted(async () => {
  await loadTree();
  refreshStatus();
  timer = setInterval(refreshStatus, 5000);
  try {
    const h = await apiGet("/api/health");
    version.value = h.version;
  } catch (_) { /* ignore */ }
});

onUnmounted(() => clearInterval(timer));
</script>

<template>
  <div id="app">
    <aside id="sidebar">
      <div class="brand">
        <h1>Obsidian Agent</h1>
        <span class="badge">v{{ version || "?" }}</span>
      </div>
      <div class="search-box">
        <input
          v-model="searchQuery"
          type="search"
          placeholder="搜索全部文档（中文分词）…"
          @keydown.enter="runSearch"
        />
      </div>
      <nav id="tree">
        <FileTree :nodes="treeNodes" @open="openFile" @load-dir="loadDir" />
      </nav>
      <footer id="status-bar">
        <div class="status-line">
          <span class="dot" :class="indexOk ? 'ok' : 'busy'"></span>
          <span>{{ indexStatus }}</span>
        </div>
        <div class="status-line">
          <span class="dot" :class="backupBusy ? 'busy' : 'ok'"></span>
          <span>{{ backupText }}</span>
          <button class="btn small" @click="backupNow">备份</button>
        </div>
      </footer>
    </aside>

    <main id="main">
      <div class="main-tabs">
        <button class="tab" :class="{ active: viewMode === 'doc' }" @click="viewMode = 'doc'">文档</button>
        <button class="tab" :class="{ active: viewMode === 'chat' }" @click="viewMode = 'chat'">Agent 对话</button>
      </div>
      <div v-if="viewMode === 'doc' && currentPath" class="doc-wrap">
        <DocView
          :path="currentPath"
          :content="docContent"
          :highlight-query="highlightQuery"
          @save="saveDoc"
        />
      </div>
      <div v-else-if="viewMode === 'search'" class="doc-wrap">
        <SearchPanel
          :results="searchResults"
          :total="searchTotal"
          :query="searchQuery"
          @select="(p) => openFile(p, searchQuery)"
        />
      </div>
      <div v-else-if="viewMode === 'chat'" class="doc-wrap chat-wrap">
        <ChatPanel />
      </div>
      <div v-else class="welcome">
        <p>从左侧选择一篇笔记，或直接搜索。</p>
      </div>
    </main>
  </div>
</template>
