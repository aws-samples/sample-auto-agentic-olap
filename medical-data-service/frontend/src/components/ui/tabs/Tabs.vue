<script setup lang="ts">
import { TabsRoot } from 'reka-ui'
const props = defineProps<{ defaultValue?: string; class?: string }>()
</script>

<template>
  <TabsRoot :default-value="props.defaultValue" :class="props.class">
    <slot />
  </TabsRoot>
</template>
