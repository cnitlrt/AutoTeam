<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-white">日志</h2>
      <div class="flex items-center gap-3">
        <label class="flex items-center gap-2 text-sm text-gray-400">
          <input type="checkbox" v-model="autoScroll" class="rounded bg-gray-800 border-gray-700" />
          自动滚动
        </label>
        <button @click="fetchLogs" :disabled="loading"
          class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-sm rounded-lg border border-gray-700 transition disabled:opacity-50 text-gray-300 hover:text-white">
          刷新
        </button>
        <button @click="clearLogs"
          class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-sm rounded-lg border border-gray-700 transition text-gray-400 hover:text-white">
          清空
        </button>
      </div>
    </div>

    <div ref="logContainer"
      class="bg-gray-950 border border-gray-800 rounded-xl p-3 md:p-4 font-mono text-xs leading-relaxed h-[calc(100vh-200px)] md:h-[600px] overflow-y-auto">
      <div v-if="logs.length === 0" class="text-gray-600 text-center py-8">暂无日志</div>
      <div v-for="(log, i) in logs" :key="i"
        class="py-0.5 flex gap-3 hover:bg-gray-900/50">
        <span class="text-gray-600 shrink-0">{{ formatTime(log.time) }}</span>
        <span class="shrink-0 w-16"
          :class="{
            'text-red-400': log.level === 'ERROR',
            'text-yellow-400': log.level === 'WARNING',
            'text-blue-400': log.level === 'INFO',
            'text-gray-500': log.level === 'DEBUG',
          }">{{ log.level }}</span>
        <span class="text-gray-300 break-all">{{ log.message }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { api } from '../api.js'

const logs = ref([])
const loading = ref(false)
const autoScroll = ref(true)
const logContainer = ref(null)
let pollTimer = null
let lastTime = 0

function formatTime(ts) {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

async function fetchLogs() {
  loading.value = true
  try {
    const result = await api.getLogs(500, lastTime)
    if (result.logs.length > 0) {
      if (lastTime === 0) {
        logs.value = result.logs
      } else {
        logs.value.push(...result.logs)
        // 保留最新 1000 条
        if (logs.value.length > 1000) {
          logs.value = logs.value.slice(-1000)
        }
      }
      lastTime = result.logs[result.logs.length - 1].time
      if (autoScroll.value) {
        nextTick(() => {
          if (logContainer.value) {
            logContainer.value.scrollTop = logContainer.value.scrollHeight
          }
        })
      }
    }
  } catch (e) {
    console.error('获取日志失败:', e)
  } finally {
    loading.value = false
  }
}

function clearLogs() {
  logs.value = []
  lastTime = 0
}

onMounted(() => {
  fetchLogs()
  pollTimer = setInterval(fetchLogs, 3000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
