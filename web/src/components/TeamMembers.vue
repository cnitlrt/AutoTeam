<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-white">Team 成员</h2>
      <button @click="fetchMembers" :disabled="loading"
        class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-sm rounded-lg border border-gray-700 transition disabled:opacity-50 text-gray-300 hover:text-white">
        {{ loading ? '加载中...' : '刷新' }}
      </button>
    </div>

    <div v-if="error" class="mb-4 px-4 py-3 rounded-lg text-sm bg-red-500/10 text-red-400 border border-red-500/20">
      {{ error }}
    </div>

    <div v-if="data" class="space-y-4">
      <!-- 统计 -->
      <div class="flex gap-4 text-sm">
        <span class="px-3 py-1.5 bg-gray-800 rounded-lg text-gray-300">成员: <span class="text-white font-medium">{{ data.total }}</span></span>
        <span v-if="data.invites > 0" class="px-3 py-1.5 bg-gray-800 rounded-lg text-gray-300">待接受邀请: <span class="text-yellow-400 font-medium">{{ data.invites }}</span></span>
      </div>

      <!-- 成员表格 -->
      <div class="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-gray-400 text-left border-b border-gray-800">
                <th class="px-4 py-3 font-medium">#</th>
                <th class="px-4 py-3 font-medium">邮箱</th>
                <th class="px-4 py-3 font-medium">角色</th>
                <th class="px-4 py-3 font-medium">类型</th>
                <th class="px-4 py-3 font-medium">来源</th>
                <th class="px-4 py-3 font-medium text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(m, i) in data.members" :key="m.email + m.type"
                class="border-b border-gray-800/50 hover:bg-gray-800/30 transition">
                <td class="px-4 py-3 text-gray-500">{{ i + 1 }}</td>
                <td class="px-4 py-3 font-mono text-xs text-slate-200">{{ m.email }}</td>
                <td class="px-4 py-3">
                  <span class="px-2 py-0.5 rounded text-xs font-medium"
                    :class="{
                      'bg-purple-500/10 text-purple-400': m.role === 'account-owner',
                      'bg-blue-500/10 text-blue-400': m.role === 'account-admin',
                      'bg-gray-500/10 text-gray-300': m.role !== 'account-owner' && m.role !== 'account-admin',
                    }">
                    {{ m.role || 'member' }}
                  </span>
                </td>
                <td class="px-4 py-3">
                  <span class="px-2 py-0.5 rounded text-xs font-medium"
                    :class="m.type === 'invite' ? 'bg-yellow-500/10 text-yellow-400' : 'bg-green-500/10 text-green-400'">
                    {{ m.type === 'invite' ? '待接受' : '已加入' }}
                  </span>
                </td>
                <td class="px-4 py-3">
                  <span class="text-xs" :class="m.is_local ? 'text-blue-400' : 'text-gray-500'">
                    {{ m.is_local ? '本地管理' : '外部' }}
                  </span>
                </td>
                <td class="px-4 py-3 text-right">
                  <button
                    v-if="m.role !== 'account-owner'"
                    @click="removeMember(m)"
                    :disabled="removingId === memberKey(m)"
                    class="px-3 py-1.5 rounded-lg text-xs font-medium border transition"
                    :class="removingId === memberKey(m)
                      ? 'bg-gray-800 text-gray-500 border-gray-700 cursor-not-allowed'
                      : 'bg-rose-600/10 text-rose-400 border-rose-500/30 hover:bg-rose-600/20'"
                  >
                    {{ removingId === memberKey(m) ? '处理中...' : '移出' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Loading -->
    <div v-else-if="loading" class="bg-gray-900 border border-gray-800 rounded-xl h-64 animate-pulse"></div>

    <!-- Empty -->
    <div v-else class="text-center text-gray-500 py-12">
      点击「刷新」加载 Team 成员列表
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const data = ref(null)
const loading = ref(false)
const error = ref('')
const removingId = ref('')

const CACHE_KEY = 'autoteam_team_members'

function loadCache() {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (raw) {
      const cached = JSON.parse(raw)
      // 缓存 10 分钟有效
      if (cached.time && Date.now() - cached.time < 600000) {
        return cached.data
      }
    }
  } catch {}
  return null
}

function saveCache(d) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({ data: d, time: Date.now() }))
  } catch {}
}

function memberKey(member) {
  return `${member.type}:${member.user_id}:${member.email}`
}

async function fetchMembers() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.getTeamMembers()
    saveCache(data.value)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function removeMember(member) {
  const actionText = member.type === 'invite' ? '取消邀请' : '移出 Team'
  const ok = window.confirm(`确认${actionText} ${member.email}？`)
  if (!ok) return

  removingId.value = memberKey(member)
  error.value = ''
  try {
    await api.removeTeamMember({
      email: member.email,
      user_id: member.user_id,
      type: member.type,
    })
    try {
      localStorage.removeItem(CACHE_KEY)
    } catch {}
    await fetchMembers()
  } catch (e) {
    error.value = e.message
  } finally {
    removingId.value = ''
  }
}

onMounted(() => {
  const cached = loadCache()
  if (cached) {
    data.value = cached
  } else {
    fetchMembers()
  }
})
</script>
