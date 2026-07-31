/* 轻量 Obsidian Markdown 渲染器（零依赖，M4 静态方案；M6 可升级 Quartz 插件）
 * 安全：先 escapeHTML 再应用语法标记，杜绝 XSS 注入。
 * 覆盖：frontmatter / 标题 / 代码块 / callout / wikilink / 高亮 / 表格 / 列表 / 引用 / 注释剥离
 */
(function (global) {
  "use strict";

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function extractFrontmatter(src) {
    const lines = src.split(/\r?\n/);
    if (lines[0] && lines[0].trim() === "---") {
      const end = lines.slice(1).findIndex((l) => l.trim() === "---");
      if (end >= 0) {
        const fmLines = lines.slice(1, end + 1);
        const body = lines.slice(end + 2).join("\n");
        return { fm: parseFm(fmLines), body };
      }
    }
    return { fm: null, body: src };
  }

  function parseFm(lines) {
    const fm = {};
    let key = null, list = [];
    const flush = () => { if (key !== null) { fm[key] = list.length ? list : ""; key = null; list = []; } };
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

  function fmCard(fm) {
    const rows = Object.entries(fm)
      .map(([k, v]) => {
        const val = Array.isArray(v) ? v.join(", ") : String(v);
        return `<div class="fm-row"><span class="fm-key">${escapeHtml(k)}</span><span class="fm-val">${escapeHtml(val)}</span></div>`;
      })
      .join("");
    return `<details class="frontmatter"><summary>frontmatter (${Object.keys(fm).length})</summary>${rows}</details>`;
  }

  /* 行内渲染：escape 后替换 */
  function renderInline(text) {
    let t = escapeHtml(text);
    // 行内代码
    t = t.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
    // 高亮 ==xx==
    t = t.replace(/==([^=]+)==/g, (_, c) => `<mark>${c}</mark>`);
    // 粗体 **xx**（非贪婪，避免跨行）
    t = t.replace(/\*\*([^*]+)\*\*/g, (_, c) => `<strong>${c}</strong>`);
    // wikilink [[目标|别名]] / [[目标#标题]] / [[目标]]
    t = t.replace(/\[\[([^\]|#]+?)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/g, (_, target, alias) => {
      const label = alias || target;
      const tgt = target.replace(/\.md$/i, "");
      return `<a class="wikilink" data-path="${escapeHtml(tgt + ".md")}">${escapeHtml(label)}</a>`;
    });
    // 普通链接 [text](url)
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, label, url) =>
      `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${label}</a>`
    );
    // 内联标签 #tag（非标题、非 URL 内）
    t = t.replace(/(^|[\s(])#([\w\u4e00-\u9fff][\w\u4e00-\u9fff\/-]*)/g, (_, pre, tag) =>
      `${pre}<span class="tag">#${escapeHtml(tag)}</span>`
    );
    return t;
  }

  function renderCallout(lines, i) {
    // lines[i] 以 "> [!type]" 开头，收集连续 "> " 行
    const head = lines[i].trim();
    const m = head.match(/^> \[!(\w+)\]([+-]?)\s*(.*)$/);
    if (!m) return null;
    const type = m[1].toLowerCase();
    const title = m[3] || type;
    const block = [`> ${lines[i].slice(2)}`];
    let j = i + 1;
    while (j < lines.length && /^>\s?/.test(lines[j])) {
      block.push(lines[j]);
      j++;
    }
    const inner = block.map((l) => l.replace(/^>\s?/, "")).join("\n");
    return {
      html:
        `<div class="callout callout-${escapeHtml(type)}">` +
        `<div class="callout-title">${renderInline(title)}</div>` +
        `<div class="callout-body">${renderBlocks(inner)}</div></div>`,
      next: j,
    };
  }

  function renderBlocks(src) {
    const lines = String(src).split(/\r?\n/);
    let html = "";
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];

      // 注释块 %%...%%
      if (/^\s*%%/.test(line)) {
        i++;
        while (i < lines.length && !/%%/.test(lines[i])) i++;
        i++;
        continue;
      }

      // 代码块
      const fence = line.match(/^```(\w*)\s*$/);
      if (fence) {
        const lang = fence[1];
        const buf = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) {
          buf.push(lines[i]);
          i++;
        }
        i++; // 跳过结束 ```
        html += `<pre class="code-block" data-lang="${escapeHtml(lang)}"><code>${escapeHtml(buf.join("\n"))}</code></pre>`;
        continue;
      }

      // 标题
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        const lvl = h[1].length;
        html += `<h${lvl}>${renderInline(h[2])}</h${lvl}>`;
        i++;
        continue;
      }

      // 分隔线
      if (/^\s*---+\s*$/.test(line) || /^\s*\*\*\*+\s*$/.test(line)) {
        html += "<hr/>";
        i++;
        continue;
      }

      // callout
      if (/^>\s*\[!/.test(line)) {
        const out = renderCallout(lines, i);
        if (out) {
          html += out.html;
          i = out.next;
          continue;
        }
      }

      // 引用块
      if (/^>\s?/.test(line)) {
        const buf = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          buf.push(lines[i].replace(/^>\s?/, ""));
          i++;
        }
        html += `<blockquote>${renderBlocks(buf.join("\n"))}</blockquote>`;
        continue;
      }

      // 列表（无序）
      if (/^\s*[-*+]\s+/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
          const item = lines[i].replace(/^\s*[-*+]\s+/, "");
          const task = item.match(/^\[( |x|X)\]\s*(.*)$/);
          if (task) {
            const checked = task[1].toLowerCase() === "x";
            buf.push(`<li class="task-item"><input type="checkbox" ${checked ? "checked" : ""} disabled/> ${renderInline(task[2])}</li>`);
          } else {
            buf.push(`<li>${renderInline(item)}</li>`);
          }
          i++;
        }
        html += `<ul>${buf.join("")}</ul>`;
        continue;
      }

      // 有序列表
      if (/^\s*\d+\.\s+/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          buf.push(`<li>${renderInline(lines[i].replace(/^\s*\d+\.\s+/, ""))}</li>`);
          i++;
        }
        html += `<ol>${buf.join("")}</ol>`;
        continue;
      }

      // 表格（简单：| a | b | 表头分隔行）
      if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|?[\s\-:|]+\|?\s*$/.test(lines[i + 1]) && /-/.test(lines[i + 1])) {
        const headers = line.split("|").map((s) => s.trim()).filter(Boolean);
        const rows = [];
        i += 2;
        while (i < lines.length && /^\s*\|/.test(lines[i])) {
          rows.push(lines[i].split("|").map((s) => renderInline(s.trim())).filter((_, idx) => idx > 0 && idx <= headers.length));
          i++;
        }
        let t = "<table><thead><tr>" + headers.map((h) => `<th>${renderInline(h)}</th>`).join("") + "</tr></thead><tbody>";
        for (const r of rows) t += "<tr>" + headers.map((_, ci) => `<td>${r[ci] || ""}</td>`).join("") + "</tr>";
        html += t + "</tbody></table>";
        continue;
      }

      // 空行
      if (!line.trim()) {
        i++;
        continue;
      }

      // 段落：收集到空行
      const buf = [line];
      i++;
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|>\s?\[!|\s*[-*+]\s|\s*\d+\.\s)/.test(lines[i])) {
        buf.push(lines[i]);
        i++;
      }
      html += `<p>${renderInline(buf.join("\n"))}</p>`;
    }
    return html;
  }

  function render(src) {
    const { fm, body } = extractFrontmatter(String(src));
    let html = "";
    if (fm && Object.keys(fm).length) html += fmCard(fm);
    html += renderBlocks(body);
    return html;
  }

  global.MdRender = { render, escapeHtml };
})(window);
