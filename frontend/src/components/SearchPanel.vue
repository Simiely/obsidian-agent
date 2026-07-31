<script setup>
// 搜索结果面板
import { esc } from "../md.js";

const props = defineProps({
  results: { type: Array, default: () => [] },
  total: { type: Number, default: 0 },
  query: { type: String, default: "" },
});
const emit = defineEmits(["select"]);
</script>

<template>
  <section class="search-panel">
    <div v-if="total === 0" class="empty">未找到与「{{ query }}」相关的结果</div>
    <template v-else>
      <div class="search-meta">共 {{ total }} 条结果（{{ query }}）</div>
      <div
        v-for="r in results"
        :key="r.path"
        class="search-hit"
        @click="emit('select', r.path)"
      >
        <div class="hit-title">{{ r.title }}</div>
        <div class="hit-path">{{ r.path }}</div>
        <div v-if="r.snippets && r.snippets[0]" class="hit-snippet" v-html="esc(r.snippets[0].text)"></div>
        <span v-for="t in r.tags" :key="t" class="tag">#{{ t }}</span>
      </div>
    </template>
  </section>
</template>
