// Markdown 渲染：markdown-it + Obsidian 方言（wikilink / callout / 高亮 / frontmatter）
import MarkdownIt from "markdown-it";

const md = new MarkdownIt({
  html: false,
  linkify: false,
  breaks: true,
  highlight: (str, lang) => {
    if (lang && md.options.highlight) {
      return `<pre class="code-block" data-lang="${md.utils.escapeHtml(lang)}"><code>${md.utils.escapeHtml(str)}</code></pre>`;
    }
    return `<pre class="code-block"><code>${md.utils.escapeHtml(str)}</code></pre>`;
  },
});

// ---- 插件：wikilink [[目标|别名]] ----
md.inline.ruler.before("link", "wikilink", (state, silent) => {
  const m = state.src.slice(state.pos).match(/^\[\[([^\]|#]+?)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/);
  if (!m) return false;
  if (silent) return true;
  const target = m[1].replace(/\.md$/i, "");
  const label = m[2] || target;
  const open = state.push("wikilink_open", "a", 1);
  open.attrSet("class", "wikilink");
  open.attrSet("data-path", target + ".md");
  const text = state.push("text", "", 0);
  text.content = label;
  state.push("wikilink_close", "a", -1);
  state.pos += m[0].length;
  return true;
});

// ---- 插件：高亮 ==文字== ----
md.inline.ruler.before("emphasis", "highlight", (state, silent) => {
  const m = state.src.slice(state.pos).match(/^==([^=]+)==/);
  if (!m) return false;
  if (silent) return true;
  state.push("highlight_open", "mark", 1);
  const t = state.push("text", "", 0);
  t.content = m[1];
  state.push("highlight_close", "mark", -1);
  state.pos += m[0].length;
  return true;
});

// ---- 渲染入口 ----
export function renderMarkdown(src, opts = {}) {
  let text = String(src);
  text = text.replace(/%%[\s\S]*?%%/g, ""); // Obsidian 注释
  const fm = extractFrontmatter(text);
  const bodyHtml = md.render(fm.body);
  let html = "";
  if (fm.data && Object.keys(fm.data).length) {
    const rows = Object.entries(fm.data)
      .map(([k, v]) => `<div class="fm-row"><span class="fm-key">${esc(k)}</span><span class="fm-val">${esc(Array.isArray(v) ? v.join(", ") : String(v))}</span></div>`)
      .join("");
    html += `<details class="frontmatter"><summary>frontmatter (${Object.keys(fm.data).length})</summary>${rows}</details>`;
  }
  html += bodyHtml;
  return html;
}

function extractFrontmatter(src) {
  const lines = src.split(/\r?\n/);
  if (lines[0] && lines[0].trim() === "---") {
    const end = lines.slice(1).findIndex((l) => l.trim() === "---");
    if (end >= 0) {
      return { data: parseFm(lines.slice(1, end + 1)), body: lines.slice(end + 2).join("\n") };
    }
  }
  return { data: null, body: src };
}

function parseFm(lines) {
  const fm = {};
  let key = null, list = [];
  const flush = () => {
    if (key !== null) {
      fm[key] = list.length ? list : "";
      key = null;
      list = [];
    }
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("- ")) {
      if (key !== null) list.push(line.slice(2).replace(/^["']|["']$/g, ""));
      continue;
    }
    const i = line.indexOf(":");
    if (i > 0) {
      flush();
      key = line.slice(0, i).trim();
      let v = line.slice(i + 1).trim();
      if (v.startsWith("[") && v.endsWith("]")) {
        fm[key] = v.slice(1, -1).split(",").map((s) => s.trim()).filter(Boolean);
        key = null;
      } else if (v) {
        fm[key] = v.replace(/^["']|["']$/g, "");
        key = null;
      }
    }
  }
  flush();
  return fm;
}

export function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- 渲染后 DOM 增强：callout 转换 + 任务列表 + 搜索高亮 ----
export function enhanceDom(root, query) {
  // callout：blockquote 首行 [!type]
  root.querySelectorAll("blockquote").forEach((bq) => {
    const first = bq.firstElementChild;
    if (!first) return;
    const m = first.textContent.match(/^\[!(\w+)\]([+-]?)\s*(.*)$/);
    if (!m) return;
    const type = m[1].toLowerCase();
    const title = m[3] || m[1];
    if (first.tagName === "P") first.remove();
    bq.classList.add("callout", `callout-${type}`);
    const titleDiv = document.createElement("div");
    titleDiv.className = "callout-title";
    titleDiv.textContent = title;
    bq.prepend(titleDiv);
  });

  // 任务列表 - [ ] / - [x]
  root.querySelectorAll("li").forEach((li) => {
    const m = li.textContent.match(/^\[([ xX])\]\s+(.*)$/);
    if (!m) return;
    li.classList.add("task-item");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = m[1].toLowerCase() === "x";
    cb.disabled = true;
    li.textContent = m[2];
    li.prepend(cb);
  });

  // 搜索高亮 + 滚动定位
  if (query) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let n;
    while ((n = walker.nextNode())) if (n.textContent.includes(query)) nodes.push(n);
    if (nodes.length) {
      const first = nodes[0];
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
  }
}
