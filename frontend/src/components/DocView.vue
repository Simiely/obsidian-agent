<script setup>
// 文档视图：阅读（渲染）/ 编辑（纯文本原样保存，坑 #1：不做 WYSIWYG）
import { ref, watch, nextTick, computed } from "vue";
import { renderMarkdown, enhanceDom } from "../md.js";

const props = defineProps({
  path: { type: String, default: "" },
  content: { type: String, default: "" },
  highlightQuery: { type: String, default: "" },
});
const emit = defineEmits(["save"]);

const mode = ref("read");
const draft = ref("");
const viewEl = ref(null);

const html = computed(() => (props.content ? renderMarkdown(props.content) : ""));

watch(
  () => [props.path, props.content],
  () => {
    mode.value = "read";
    draft.value = props.content;
  },
  { immediate: true }
);

watch(
  () => html.value,
  async () => {
    await nextTick();
    if (viewEl.value) {
      enhanceDom(viewEl.value, props.highlightQuery);
    }
  }
);

function startEdit() {
  draft.value = props.content;
  mode.value = "edit";
}

function cancel() {
  mode.value = "read";
}

function save() {
  if (draft.value !== props.content) emit("save", draft.value);
  else mode.value = "read";
}

function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {
    e.preventDefault();
    save();
  }
}
</script>

<template>
  <div class="doc">
    <div class="doc-header">
      <span class="doc-path" :title="path">{{ path }}</span>
      <div class="actions">
        <button v-if="mode === 'read'" class="btn primary" @click="startEdit">编辑</button>
        <template v-else>
          <button class="btn primary" @click="save">保存</button>
          <button class="btn" @click="cancel">取消</button>
        </template>
      </div>
    </div>
    <article v-if="mode === 'read'" ref="viewEl" class="markdown-body" v-html="html"></article>
    <textarea
      v-else
      v-model="draft"
      class="editor"
      spellcheck="false"
      @keydown="onKeydown"
    ></textarea>
  </div>
</template>
