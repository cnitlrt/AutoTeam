<template>
  <div class="max-w-7xl mx-auto px-4 py-6">
    <!-- Header -->
    <header class="flex items-center justify-between mb-8">
      <div>
        <h1 class="text-2xl font-bold text-white">AutoTeam</h1>
        <p class="text-sm text-gray-400 mt-1">ChatGPT Team 账号自动轮转管理</p>
      </div>
      <div class="flex items-center gap-3">
        <span v-if="runningTask" class="flex items-center gap-2 text-sm text-yellow-400">
          <span class="animate-spin inline-block w-4 h-4 border-2 border-yellow-400 border-t-transparent rounded-full"></span>
          {{ runningTask.command }} 执行中...
        </span>
        <button @click="refresh" :disabled="loading"
          class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-sm rounded-lg border border-gray-700 transition disabled:opacity-50">
          {{ loading ? '刷新中...' : '刷新' }}
        </button>
      </div>
    </header>

    <!-- Dashboard -->
    <Dashboard :status="status" :loading="loading" @refresh="refresh" />

    <!-- Task Panel -->
    <TaskPanel :running-task="runningTask" @task-started="onTaskStarted" @refresh="refresh" />

    <!-- Task History -->
    <TaskHistory :tasks="tasks" />

    <!-- Settings -->
    <Settings />
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from './api.js'
import Dashboard from './components/Dashboard.vue'
import TaskPanel from './components/TaskPanel.vue'
import TaskHistory from './components/TaskHistory.vue'
import Settings from './components/Settings.vue'

const status = ref(null)
const tasks = ref([])
const loading = ref(false)
const runningTask = ref(null)

let pollTimer = null

async function refresh() {
  loading.value = true
  try {
    const [s, t] = await Promise.all([api.getStatus(), api.getTasks()])
    status.value = s
    tasks.value = t
    runningTask.value = t.find(t => t.status === 'running' || t.status === 'pending') || null
  } catch (e) {
    console.error('刷新失败:', e)
  } finally {
    loading.value = false
  }
}

function onTaskStarted() {
  // 任务启动后开始较快轮询
  startPolling(10000)
  refresh()
}

function startPolling(interval = 600000) {
  stopPolling()
  pollTimer = setInterval(async () => {
    await refresh()
    // 如果没有运行中的任务，降回慢轮询
    if (!runningTask.value && interval < 600000) {
      startPolling(600000)
    }
  }, interval)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(() => {
  refresh()
  startPolling(600000)
})

onUnmounted(() => {
  stopPolling()
})
</script>
