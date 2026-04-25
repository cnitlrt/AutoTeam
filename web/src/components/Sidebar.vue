<template>
  <!-- 桌面端侧边栏 -->
  <nav class="sticky top-0 hidden min-h-screen w-72 shrink-0 flex-col border-r border-white/10 bg-slate-950/65 p-5 backdrop-blur-2xl md:flex">
    <div class="mb-8">
      <div class="mb-5 flex items-center gap-3">
        <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500/30 to-cyan-500/20 text-2xl shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]">
          ⚡
        </div>
        <div>
          <h1 class="text-lg font-semibold tracking-tight text-white">AutoTeam</h1>
          <p class="mt-0.5 text-xs text-slate-400">账号轮转管理中心</p>
        </div>
      </div>

      <div class="glass-card-soft px-4 py-3">
        <div class="flex items-center gap-2 text-sm text-slate-200">
          <span class="inline-block h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_14px_rgba(52,211,153,0.85)]"></span>
          面板在线
        </div>
        <p class="mt-1 text-xs leading-5 text-slate-400">统一查看仪表盘、配置、同步、OAuth 和日志。</p>
      </div>
    </div>

    <div class="flex-1 space-y-2">
      <button v-for="item in items" :key="item.key"
        @click="$emit('navigate', item.key)"
        class="group flex w-full items-center gap-3 rounded-2xl px-3 py-3 text-left transition"
        :class="active === item.key
          ? 'bg-blue-500/15 text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] ring-1 ring-blue-400/20'
          : 'text-slate-400 hover:bg-white/5 hover:text-white'"
      >
        <span
          class="flex h-11 w-11 items-center justify-center rounded-2xl border text-lg transition"
          :class="active === item.key
            ? 'border-blue-400/20 bg-blue-500/15 text-blue-200'
            : 'border-white/10 bg-white/5 text-slate-300 group-hover:border-white/20 group-hover:bg-white/10'"
        >
          {{ item.icon }}
        </span>
        <span class="min-w-0 flex-1">
          <span class="block text-sm font-medium">{{ item.label }}</span>
          <span class="mt-0.5 block text-xs text-slate-500 group-hover:text-slate-400">{{ item.hint }}</span>
        </span>
        <span
          class="h-2.5 w-2.5 rounded-full transition"
          :class="active === item.key ? 'bg-cyan-300 shadow-[0_0_14px_rgba(103,232,249,0.9)]' : 'bg-slate-700 group-hover:bg-slate-500'"
        ></span>
      </button>
    </div>

    <div class="space-y-2 border-t border-white/10 pt-5">
      <button @click="$emit('refresh')" :disabled="loading"
        class="btn-secondary w-full justify-start gap-3 rounded-2xl px-3 py-3 text-left disabled:opacity-50">
        <span class="text-base">🔄</span>
        {{ loading ? '刷新中...' : '刷新数据' }}
      </button>
      <button v-if="authRequired" @click="$emit('logout')"
        class="btn-danger w-full justify-start gap-3 rounded-2xl px-3 py-3 text-left">
        <span class="text-base">🚪</span>
        登出
      </button>
    </div>
  </nav>

  <!-- 移动端底部 tab 栏 -->
  <nav class="fixed bottom-3 left-3 right-3 z-50 flex rounded-3xl border border-white/10 bg-slate-950/80 p-1.5 shadow-[0_20px_40px_-20px_rgba(15,23,42,0.9)] backdrop-blur-2xl md:hidden">
    <button v-for="item in items" :key="item.key"
      @click="$emit('navigate', item.key)"
      class="flex-1 rounded-2xl px-1 py-2 text-xs transition"
      :class="active === item.key
        ? 'bg-blue-500/15 text-blue-300'
        : 'text-slate-500'">
      <div class="flex flex-col items-center">
        <span class="text-lg">{{ item.icon }}</span>
        <span class="mt-0.5">{{ item.mobileLabel || item.label }}</span>
      </div>
    </button>
  </nav>
</template>

<script setup>
defineProps({
  active: String,
  loading: Boolean,
  authRequired: Boolean,
})
defineEmits(['navigate', 'refresh', 'logout'])

const items = [
  { key: 'dashboard', icon: '📊', label: '仪表盘', mobileLabel: '仪表盘', hint: '概览账号池与状态' },
  { key: 'config', icon: '🧩', label: '配置面板', mobileLabel: '配置', hint: '统一编辑系统配置' },
  { key: 'team', icon: '👥', label: 'Team 成员', mobileLabel: '成员', hint: '查看与管理成员' },
  { key: 'pool', icon: '🔁', label: '账号池操作', mobileLabel: '账号池', hint: '轮转、补位与清理' },
  { key: 'sync', icon: '🔄', label: '同步中心', mobileLabel: '同步', hint: '同步本地、远端与状态' },
  { key: 'oauth', icon: '🔐', label: 'OAuth 登录', mobileLabel: 'OAuth', hint: '手动接管 OAuth 流程' },
  { key: 'tasks', icon: '📜', label: '任务历史', mobileLabel: '任务', hint: '追踪任务执行结果' },
  { key: 'logs', icon: '📋', label: '日志', mobileLabel: '日志', hint: '查看实时运行日志' },
]
</script>
