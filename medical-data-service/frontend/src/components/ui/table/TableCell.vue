<script setup lang="ts">
import { cn } from '@/lib/utils'
const props = defineProps<{ class?: string }>()
</script>

<template>
  <td :class="cn('p-3 align-middle', props.class)">
    <slot />
  </td>
</template>
