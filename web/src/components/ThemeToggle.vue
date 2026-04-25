<template>
  <button
    type="button"
    class="theme-toggle"
    :class="{ 'theme-toggle-floating': floating }"
    :title="isDark ? '切换到白天模式' : '切换到夜间模式'"
    :aria-label="isDark ? '切换到白天模式' : '切换到夜间模式'"
    @click="toggleTheme"
  >
    <span class="text-base">{{ isDark ? '🌙' : '☀️' }}</span>
    <span class="hidden sm:inline">{{ isDark ? '夜间' : '白天' }}</span>
  </button>
</template>

<script setup>
import { useTheme } from '../theme.js'

defineProps({
  floating: {
    type: Boolean,
    default: false,
  },
})

const { isDark, toggleTheme } = useTheme()
</script>
