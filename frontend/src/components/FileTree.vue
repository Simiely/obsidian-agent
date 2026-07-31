<script setup>
// 递归文件树（懒加载展开）
import { ref } from "vue";

defineOptions({ name: "FileTree" });
const props = defineProps({
  nodes: { type: Array, default: () => [] },
});
const emit = defineEmits(["open", "load-dir"]);

const expanded = ref({});

async function toggleDir(node) {
  const key = node.path;
  expanded.value = { ...expanded.value, [key]: !expanded.value[key] };
  if (!node.childrenLoaded) {
    emit("load-dir", node.path);
  }
}

function openFile(node) {
  emit("open", node.path);
}
</script>

<template>
  <div class="tree">
    <div v-for="node in nodes" :key="node.path" class="tree-node">
      <div
        v-if="node.type === 'dir'"
        class="tree-row"
        @click="toggleDir(node)"
      >
        <span class="twisty" :class="{ open: expanded[node.path] }">▸</span>
        <span class="tree-name">{{ node.name }}</span>
      </div>
      <div v-else class="tree-row tree-file" @click="openFile(node)">
        <span class="twisty-spacer"></span>
        <span class="tree-name">{{ node.name }}</span>
      </div>
      <div v-if="node.type === 'dir' && expanded[node.path]" class="tree-children">
        <FileTree :nodes="node.children || []" @open="openFile" @load-dir="emit('load-dir', $event)" />
      </div>
    </div>
  </div>
</template>
