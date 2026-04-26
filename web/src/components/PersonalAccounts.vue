<template>
  <div v-if="status">
    <div class="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 gap-4 mb-6">
      <div v-for="card in cards" :key="card.label" class="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <div class="text-sm text-gray-400">{{ card.label }}</div>
        <div class="text-3xl font-bold mt-1" :class="card.color">{{ card.value }}</div>
      </div>
    </div>

    <div class="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div class="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
        <div>
          <h2 class="text-lg font-semibold text-white">个人账号管理</h2>
          <p class="mt-1 text-xs text-gray-400">手动登录 / 重登 Personal 个人账号，并查看个人额度状态。</p>
        </div>
      </div>

      <div v-if="message" class="mx-4 mt-4 px-4 py-3 rounded-lg text-sm border" :class="messageClass">
        {{ message }}
      </div>

      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-gray-400 text-left border-b border-gray-800">
              <th class="px-4 py-3 font-medium">#</th>
              <th class="px-4 py-3 font-medium">邮箱</th>
              <th class="px-4 py-3 font-medium">Team 状态</th>
              <th class="px-4 py-3 font-medium">个人状态</th>
              <th class="px-4 py-3 font-medium text-right">5h 剩余</th>
              <th class="px-4 py-3 font-medium text-right">周 剩余</th>
              <th class="px-4 py-3 font-medium">5h 重置</th>
              <th class="px-4 py-3 font-medium">周 重置</th>
              <th class="px-4 py-3 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(acc, i) in accounts"
              :key="acc.email"
              class="border-b border-gray-800/50 hover:bg-gray-800/30 transition"
            >
              <td class="px-4 py-3 text-gray-500">{{ i + 1 }}</td>
              <td class="px-4 py-3 font-mono text-xs text-slate-200">{{ acc.email }}</td>
              <td class="px-4 py-3">
                <span
                  class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium"
                  :class="statusClass(acc.status)"
                >
                  <span class="w-1.5 h-1.5 rounded-full" :class="dotClass(acc.status)"></span>
                  {{ statusLabel(acc.status) }}
                </span>
              </td>
              <td class="px-4 py-3">
                <span
                  v-if="acc.personal_status"
                  class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium"
                  :class="statusClass(acc.personal_status)"
                >
                  <span class="w-1.5 h-1.5 rounded-full" :class="dotClass(acc.personal_status)"></span>
                  {{ statusLabel(acc.personal_status) }}
                </span>
                <span v-else class="text-gray-500 text-xs">未登录</span>
              </td>
              <td class="px-4 py-3 text-right font-mono" :class="pctColor(personalQuota(acc, 'primary'))">
                {{ personalQuotaPct(acc, 'primary') }}
              </td>
              <td class="px-4 py-3 text-right font-mono" :class="pctColor(personalQuota(acc, 'weekly'))">
                {{ personalQuotaPct(acc, 'weekly') }}
              </td>
              <td class="px-4 py-3 text-gray-400 text-xs">{{ personalQuotaReset(acc, 'primary') }}</td>
              <td class="px-4 py-3 text-gray-400 text-xs">{{ personalQuotaReset(acc, 'weekly') }}</td>
              <td class="px-4 py-3 text-right">
                <button
                  @click="loginPersonalAccount(acc.email)"
                  :disabled="actionDisabled || actionEmail === acc.email"
                  class="px-3 py-1.5 rounded-lg text-xs font-medium border transition"
                  :class="actionDisabled || actionEmail === acc.email
                    ? 'bg-gray-800 text-gray-500 border-gray-700 cursor-not-allowed'
                    : 'bg-fuchsia-600/10 text-fuchsia-300 border-fuchsia-500/30 hover:bg-fuchsia-600/20'"
                >
                  {{ actionEmail === acc.email ? '登录中...' : (acc.personal_status ? '重登个人' : '登录个人') }}
                </button>
              </td>
            </tr>
            <tr v-if="accounts.length === 0">
              <td colspan="9" class="px-4 py-8 text-center text-sm text-gray-500">
                暂无可管理账号
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

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
  runningTask: Object,
})

const emit = defineEmits(['refresh'])

const actionEmail = ref('')
const message = ref('')
const messageClass = ref('')
const actionDisabled = computed(() => !!props.runningTask)

const accounts = computed(() => (props.status?.accounts || []).filter(acc => !acc.is_main_account))

const cards = computed(() => {
  if (!props.status) return []
  const s = props.status.personal_summary || {}
  return [
    { label: '个人活跃', value: s.active || 0, color: 'text-green-400' },
    { label: '待修复', value: s.auth_pending || 0, color: 'text-cyan-400' },
    { label: '待命', value: s.standby || 0, color: 'text-yellow-400' },
    { label: '额度用完', value: s.exhausted || 0, color: 'text-red-400' },
    { label: '总计', value: s.total || 0, color: 'text-white' },
  ]
})

function statusClass(s) {
  return {
    active: 'bg-green-500/10 text-green-400',
    auth_pending: 'bg-cyan-500/10 text-cyan-400',
    exhausted: 'bg-red-500/10 text-red-400',
    standby: 'bg-yellow-500/10 text-yellow-400',
    pending: 'bg-gray-500/10 text-gray-400',
  }[s] || 'bg-gray-500/10 text-gray-400'
}

function dotClass(s) {
  return {
    active: 'bg-green-400',
    auth_pending: 'bg-cyan-400',
    exhausted: 'bg-red-400',
    standby: 'bg-yellow-400',
    pending: 'bg-gray-400',
  }[s] || 'bg-gray-400'
}

function statusLabel(s) {
  return {
    active: 'Active',
    auth_pending: 'Auth pending',
    exhausted: 'Used up',
    standby: 'Standby',
    pending: 'Pending',
  }[s] || s
}

function personalQuota(acc, type) {
  const qi = props.status?.personal_quota_cache?.[acc.email] || acc.personal_last_quota
  if (!qi) return null
  const pct = type === 'primary' ? qi.primary_pct : qi.weekly_pct
  return 100 - (pct || 0)
}

function personalQuotaPct(acc, type) {
  const val = personalQuota(acc, type)
  return val !== null ? `${val}%` : '-'
}

function personalQuotaReset(acc, type) {
  const qi = props.status?.personal_quota_cache?.[acc.email] || acc.personal_last_quota
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

async function loginPersonalAccount(email) {
  if (actionDisabled.value) return

  actionEmail.value = email
  message.value = ''
  try {
    const result = await api.loginAccount(email, 'personal')
    message.value = `已提交 ${email} 的个人账号登录任务: ${result.task_id}`
    messageClass.value = 'bg-fuchsia-500/10 text-fuchsia-300 border-fuchsia-500/20'
    emit('refresh')
  } catch (e) {
    message.value = e.message
    messageClass.value = 'bg-red-500/10 text-red-400 border-red-500/20'
  } finally {
    actionEmail.value = ''
    setTimeout(() => { message.value = '' }, 8000)
  }
}
</script>
