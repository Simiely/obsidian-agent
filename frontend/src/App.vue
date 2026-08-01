<script setup>
// 应用主布局（编排层）：组合 composable + 子组件，业务逻辑全部下沉到 composables。
import { ref, onMounted, onUnmounted } from "vue";
import { API } from "./api/endpoints.js";
import FileTree from "./components/FileTree.vue";
import DocView from "./components/DocView.vue";
import SearchPanel from "./components/SearchPanel.vue";
import ChatPanel from "./components/ChatPanel.vue";
import BackupPanel from "./components/BackupPanel.vue";
import DirPicker from "./components/DirPicker.vue";
import StatusBar from "./components/StatusBar.vue";
import { apiGet, apiDelete } from "./api.js";
import { useDirPicker } from "./useDirPicker.js";
import { useStatusPolling, startStatusPolling, stopStatusPolling } from "./useStatusPolling.js";
import { useAutoBackup } from "./useAutoBackup.js";
import { useVaultDocs } from "./useVaultDocs.js";
import { useSearch } from "./useSearch.js";
import { useVaultSwitcher } from "./useVaultSwitcher.js";
import { useCreateDoc } from "./useCreateDoc.js";
import { useHashRouter } from "./useHashRouter.js";

let toastTimer = null;

function toast(msg, isError = false) {
  // 防御：toast 元素缺失时静默降级（绝不能抛错——否则会被业务 try/catch 误判为
  // "保存失败"导致状态回滚，表现为点击无响应）
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.className = isError ? "toast error" : "toast";
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 4000);
}

// 状态轮询为模块级单例（S17）：状态栏 StatusBar 独立订阅，App 不因轮询重渲染。
const { refreshStatus, version } = useStatusPolling();

// 文件树 + 文档读写
const {
  treeNodes,
  currentPath,
  docContent,
  docMeta,
  highlightQuery,
  viewMode,
  loadTree,
  loadDir,
  openFile,
  saveDoc,
  closeDoc,
} = useVaultDocs(toast);

// 搜索
const { searchResults, searchTotal, searchQuery, runSearch, reset: resetSearch } = useSearch(
  toast,
  (m) => (viewMode.value = m)
);

// 切换库：目录浏览（useDirPicker）+ 库切换（useVaultSwitcher）
const picker = useDirPicker();
const { showDialog: showVaultDialog, inputPath: vaultInput, switching: vaultSwitching } = picker;
const {
  vaultPath,
  detectedVaults,
  detectLoading,
  loadVaultPath,
  openVaultDialog,
  detectVaultsNow,
  pickVault,
  switchVault,
} = useVaultSwitcher(picker, {
  loadTree,
  openFile,
  toast,
  onSwitched: () => {
    // 切换成功后：重置搜索与视图（文档内容已由 openFile("") 清空）
    resetSearch();
    viewMode.value = "doc";
    refreshStatus();
  },
});

// 新建笔记（全局工具栏按钮）：创建在 vault 根目录，不依赖是否打开文档
const { showCreate, createName, openCreate, createNewDoc } = useCreateDoc({
  openFile,
  loadTree,
  toast,
});

// Hash 路由：视图与文档进 URL，浏览器前进/后退可用
const router = useHashRouter({ viewMode, currentPath, openFile });

// 移动端：侧栏抽屉开关（<768px 时侧栏默认隐藏）
const sidebarOpen = ref(false);

// 文档内链接点击：wikilink（全库文件名匹配）或 md 链接（已解析路径）→ 打开目标文档
async function openDocLink({ path, wikilink }) {
  if (wikilink) {
    try {
      const r = await apiGet(API.vaultResolveMd + "?name=" + encodeURIComponent(path));
      await openFile(r.path);
    } catch (e) {
      toast("找不到文档：" + path, true);
    }
  } else {
    await openFile(path);
  }
}

// 删除当前文档：删除后关闭文档视图并刷新文件树
async function deleteDoc(path) {
  try {
    await apiDelete(API.vaultFile + "?path=" + encodeURIComponent(path));
    toast(`已删除：${path}`);
    closeDoc(); // 关闭文档视图（回到空状态）
    await loadTree(); // 刷新文件树
  } catch (e) {
    toast("删除失败：" + e.message, true);
  }
}

// 活跃式自动备份（打开页面启动，活动检测+间隔备份，空闲不备份）
const autoBackup = useAutoBackup(toast);

onMounted(async () => {
  const { mode, path } = router.init();
  if (mode !== "doc") viewMode.value = mode;
  await loadTree();
  await loadVaultPath();
  startStatusPolling(); // 启动状态轮询（StatusBar 独立订阅渲染）
  refreshStatus();
  if (mode === "doc" && path) await openFile(path); // 刷新/直达：恢复上次打开的文档
  router.start();
  autoBackup.start(); // 启动自动备份（登录/打开页面即活跃起点）
});

onUnmounted(() => {
  autoBackup.stop(); // 页面关闭/刷新 → 停止（天然满足"关页面不再备份"）
  stopStatusPolling();
  router.stop();
});
</script>

<template>
  <!-- 根元素必须与 index.html 挂载点 #app 区分开（class 而非 id），
       否则 Vue 渲染出双层 #app 嵌套：内层被外层 flex 收缩到内容宽度，
       导致主区宽度随内容变化、右侧大片空白（滚动条"跳动"的真正根因）。 -->
  <div class="app-root">
    <aside id="sidebar" :class="{ open: sidebarOpen }">
      <div class="brand">
        <h1>Obsidian Agent</h1>
        <span class="badge">v{{ version || "?" }}</span>
        <button class="sidebar-close" @click="sidebarOpen = false" title="关闭侧栏">✕</button>
      </div>
      <div class="vault-bar" title="当前笔记库路径">
        <span class="vault-path">{{ vaultPath || "未选择库" }}</span>
        <button class="btn small" @click="openVaultDialog">切换库</button>
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
      <StatusBar @toast="toast" />
    </aside>

    <main id="main">
      <!-- 移动端顶栏：标题（汉堡已改左下角悬浮按钮，<768px 显示，桌面隐藏） -->
      <div class="mobile-topbar">
        <span class="topbar-title">Obsidian Agent</span>
      </div>
      <div class="main-tabs">
        <button class="btn small new-doc-btn" title="在 vault 根目录新建笔记" @click="openCreate">＋ 新建</button>
        <button class="tab" :class="{ active: viewMode === 'doc' }" @click="viewMode = 'doc'">文档</button>
        <button class="tab" :class="{ active: viewMode === 'chat' }" @click="viewMode = 'chat'">Agent 对话</button>
        <button class="tab" :class="{ active: viewMode === 'backup' }" @click="viewMode = 'backup'">备份管理</button>
      </div>
      <!-- 全局新建笔记对话框：创建在 vault 根目录（不依赖打开文档） -->
      <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
        <div class="modal confirm-modal create-modal">
          <h3>＋ 新建笔记</h3>
          <p class="modal-hint">将创建在 vault 根目录下</p>
          <input
            v-model="createName"
            class="modal-input"
            placeholder="文件名（如：我的笔记）"
            autofocus
            @keydown.enter="createNewDoc"
            @keydown.esc="showCreate = false"
          />
          <div class="confirm-actions">
            <button class="btn" @click="showCreate = false">取消</button>
            <button class="btn primary" :disabled="!createName.trim()" @click="createNewDoc">创建</button>
          </div>
        </div>
      </div>
      <div v-if="viewMode === 'doc' && currentPath" class="doc-wrap">
        <DocView
          :path="currentPath"
          :content="docContent"
          :highlight-query="highlightQuery"
          @save="saveDoc"
          @open-path="openDocLink"
          @delete-doc="deleteDoc"
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
      <div v-else-if="viewMode === 'backup'" class="doc-wrap">
        <BackupPanel :vault="vaultPath" @toast="toast" />
      </div>
      <div v-else class="welcome">
        <p>从左侧选择一篇笔记，或直接搜索。</p>
      </div>
    </main>

    <!-- 切换库对话框：自动检测列表 + 通用目录选择器（DirPicker） -->
    <div v-if="showVaultDialog" class="modal-mask" @click.self="picker.close()">
      <div class="modal vault-modal">
        <h3>切换笔记库（Vault）</h3>
        <p class="modal-hint">优先从下方「自动检测」的库中选择，也可浏览目录或手动输入路径</p>

        <!-- 自动检测到的库 -->
        <div v-if="detectLoading" class="detect-box">
          <div class="dir-empty">正在检测本机 Obsidian 库…</div>
        </div>
        <div v-else-if="detectedVaults.length" class="detect-box">
          <div class="detect-title">
            <span>📚 检测到 {{ detectedVaults.length }} 个 Obsidian 库</span>
            <button class="btn small" @click="detectVaultsNow">重新检测</button>
          </div>
          <div class="detect-list">
            <div
              v-for="v in detectedVaults"
              :key="v.path"
              class="detect-item"
              :class="{ active: vaultInput === v.path }"
              @click="pickVault(v)"
            >
              <span class="detect-name">{{ v.name }}</span>
              <span class="detect-meta">{{ v.mdCount }} 个 md</span>
              <span class="detect-path">{{ v.path }}</span>
            </div>
          </div>
        </div>

        <!-- 通用目录选择器 -->
        <DirPicker
          :show="true"
          :title="'切换笔记库'"
          :hint="'浏览目录或输入绝对路径'"
          :model-value="vaultInput"
          :switching="vaultSwitching"
          :quick-disks="picker.quickDisks.value"
          :quick-places="picker.quickPlaces.value"
          :browse-path="picker.browsePath.value"
          :browse-parent="picker.browseParent.value"
          :browse-dirs="picker.browseDirs.value"
          :browse-loading="picker.browseLoading.value"
          @update:model-value="(v) => (vaultInput = v)"
          @update:show="picker.close"
          @browse="(p) => picker.browseTo(p)"
          @quick="(item) => picker.goQuick(item)"
          @enter="(d) => picker.enterDir(d)"
          @pick="picker.pickCurrent"
          @confirm="switchVault"
        />
      </div>
    </div>

    <!-- 移动端：侧栏抽屉遮罩（点击关闭） -->
    <div v-if="sidebarOpen" class="sidebar-overlay" @click="sidebarOpen = false"></div>

    <!-- 移动端底部搜索条：仅"搜索"视图显示（<768px 显示，位于底部 Tab 上方） -->
    <div v-if="viewMode === 'search'" class="mobile-search">
      <input
        v-model="searchQuery"
        type="search"
        placeholder="搜索全部文档…"
        @keydown.enter="runSearch"
      />
    </div>

    <!-- 移动端左下角悬浮按钮：打开侧栏（空心圆图标，粗细与右侧 ☰ 一致） -->
    <button
      class="sidebar-fab"
      :class="{ 'above-search': viewMode === 'search', 'above-chat': viewMode === 'chat' }"
      title="打开侧栏"
      @click="sidebarOpen = !sidebarOpen"
    >○</button>

    <!-- 移动端底部 Tab 导航（<768px 显示，桌面隐藏） -->
    <nav class="bottom-tabs">
      <button class="tab" :class="{ active: viewMode === 'doc' }" @click="viewMode = 'doc'">📄 文档</button>
      <button class="tab" :class="{ active: viewMode === 'search' }" @click="viewMode = 'search'">🔍 搜索</button>
      <button class="tab" :class="{ active: viewMode === 'chat' }" @click="viewMode = 'chat'">💬 对话</button>
      <button class="tab" :class="{ active: viewMode === 'backup' }" @click="viewMode = 'backup'">🗄️ 备份</button>
    </nav>
  </div>
</template>
