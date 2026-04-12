<template>
  <div v-if="status">
    <!-- 统计卡片 -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
      <div v-for="card in cards" :key="card.label"
        class="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <div class="text-sm text-gray-400">{{ card.label }}</div>
        <div class="text-3xl font-bold mt-1" :class="card.color">{{ card.value }}</div>
      </div>
    </div>

    <!-- 账号表格 -->
    <div class="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div class="px-4 py-3 border-b border-gray-800">
        <h2 class="text-lg font-semibold text-white">账号列表</h2>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-gray-400 text-left border-b border-gray-800">
              <th class="px-4 py-3 font-medium">#</th>
              <th class="px-4 py-3 font-medium">邮箱</th>
              <th class="px-4 py-3 font-medium">状态</th>
              <th class="px-4 py-3 font-medium text-right">5h 剩余</th>
              <th class="px-4 py-3 font-medium text-right">周 剩余</th>
              <th class="px-4 py-3 font-medium">5h 重置</th>
              <th class="px-4 py-3 font-medium">周 重置</th>
              <th class="px-4 py-3 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(acc, i) in status.accounts" :key="acc.email"
              class="border-b border-gray-800/50 hover:bg-gray-800/30 transition">
              <td class="px-4 py-3 text-gray-500">{{ i + 1 }}</td>
              <td class="px-4 py-3 font-mono text-xs">{{ acc.email }}</td>
              <td class="px-4 py-3">
                <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium"
                  :class="statusClass(acc.status)">
                  <span class="w-1.5 h-1.5 rounded-full" :class="dotClass(acc.status)"></span>
                  {{ statusLabel(acc.status) }}
                </span>
              </td>
              <td class="px-4 py-3 text-right font-mono" :class="pctColor(quota(acc, 'primary'))">
                {{ quotaPct(acc, 'primary') }}
              </td>
              <td class="px-4 py-3 text-right font-mono" :class="pctColor(quota(acc, 'weekly'))">
                {{ quotaPct(acc, 'weekly') }}
              </td>
              <td class="px-4 py-3 text-gray-400 text-xs">{{ quotaReset(acc, 'primary') }}</td>
              <td class="px-4 py-3 text-gray-400 text-xs">{{ quotaReset(acc, 'weekly') }}</td>
              <td class="px-4 py-3 text-right">
                <button @click="deleteAccount(acc)" :disabled="deletingEmail === acc.email"
                  class="px-2 py-1 text-xs rounded border border-red-500/30 text-red-400 hover:bg-red-500/10 transition disabled:opacity-50">
                  {{ deletingEmail === acc.email ? '删除中' : '删除' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Loading skeleton -->
  <div v-else-if="loading" class="space-y-4">
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-4">
      <div v-for="i in 4" :key="i" class="bg-gray-900 border border-gray-800 rounded-xl p-4 h-20 animate-pulse"></div>
    </div>
    <div class="bg-gray-900 border border-gray-800 rounded-xl h-64 animate-pulse"></div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({
  status: Object,
  loading: Boolean,
})

const emit = defineEmits(['refresh'])
const deletingEmail = ref('')

const cards = computed(() => {
  if (!props.status) return []
  const s = props.status.summary
  return [
    { label: '活跃', value: s.active, color: 'text-green-400' },
    { label: '待命', value: s.standby, color: 'text-yellow-400' },
    { label: '额度用完', value: s.exhausted, color: 'text-red-400' },
    { label: '总计', value: s.total, color: 'text-white' },
  ]
})

function statusClass(s) {
  return {
    active: 'bg-green-500/10 text-green-400',
    exhausted: 'bg-red-500/10 text-red-400',
    standby: 'bg-yellow-500/10 text-yellow-400',
    pending: 'bg-gray-500/10 text-gray-400',
  }[s] || 'bg-gray-500/10 text-gray-400'
}

function dotClass(s) {
  return {
    active: 'bg-green-400',
    exhausted: 'bg-red-400',
    standby: 'bg-yellow-400',
    pending: 'bg-gray-400',
  }[s] || 'bg-gray-400'
}

function statusLabel(s) {
  return { active: 'Active', exhausted: 'Used up', standby: 'Standby', pending: 'Pending' }[s] || s
}

function quota(acc, type) {
  const qi = props.status?.quota_cache?.[acc.email] || acc.last_quota
  if (!qi) return null
  const pct = type === 'primary' ? qi.primary_pct : qi.weekly_pct
  return 100 - (pct || 0)
}

function quotaPct(acc, type) {
  const val = quota(acc, type)
  return val !== null ? `${val}%` : '-'
}

function quotaReset(acc, type) {
  const qi = props.status?.quota_cache?.[acc.email] || acc.last_quota
  if (!qi) return '-'
  const ts = type === 'primary' ? qi.primary_resets_at : qi.weekly_resets_at
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function pctColor(val) {
  if (val === null) return 'text-gray-500'
  if (val > 30) return 'text-green-400'
  if (val > 0) return 'text-yellow-400'
  return 'text-red-400'
}

async function deleteAccount(acc) {
  if (!window.confirm(`删除本地账号记录？\n${acc.email}`)) return
  deletingEmail.value = acc.email
  try {
    await api.deleteAccount(acc.email)
    emit('refresh')
  } catch (e) {
    window.alert(e.message || '删除失败')
  } finally {
    deletingEmail.value = ''
  }
}
</script>
