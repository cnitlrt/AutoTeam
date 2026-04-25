<template>
  <div class="mt-6 space-y-6">
    <div class="glass-card overflow-hidden p-6">
      <div class="pointer-events-none absolute"></div>
      <div class="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div class="mb-3 inline-flex items-center gap-2 rounded-full border border-blue-400/20 bg-blue-500/10 px-4 py-2 text-sm text-blue-200">
            <span class="inline-block h-2 w-2 rounded-full bg-cyan-400 shadow-[0_0_14px_rgba(34,211,238,0.9)]"></span>
            AutoTeam Configuration Center
          </div>
          <h2 class="section-heading">配置面板</h2>
          <p class="section-subtitle max-w-2xl">
            参考 CLIProxyAPI 的方式，分为可视化编辑和源文件编辑两种模式。
          </p>
        </div>

        <div class="flex flex-wrap gap-2">
          <button
            v-for="item in editModes"
            :key="item.key"
            @click="editMode = item.key"
            class="pill-tab flex items-center gap-2"
            :class="editMode === item.key
              ? 'pill-tab-active'
              : ''"
          >
            <span class="text-base">{{ item.icon }}</span>
            {{ item.label }}
          </button>
        </div>
      </div>

      <div class="mt-6 grid gap-4 md:grid-cols-3">
        <div class="glass-card-soft p-4">
          <div class="text-2xl">🧩</div>
          <div class="mt-3 text-sm font-medium text-white">统一配置入口</div>
          <div class="mt-1 text-xs leading-5 text-slate-400">运行参数、管理员登录、巡检设置都集中在这里。</div>
        </div>
        <div class="glass-card-soft p-4">
          <div class="text-2xl">⚙️</div>
          <div class="mt-3 text-sm font-medium text-white">可视化编辑</div>
          <div class="mt-1 text-xs leading-5 text-slate-400">适合日常修改，分类更清晰，保存后立刻生效。</div>
        </div>
        <div class="glass-card-soft p-4">
          <div class="text-2xl">📝</div>
          <div class="mt-3 text-sm font-medium text-white">源文件编辑</div>
          <div class="mt-1 text-xs leading-5 text-slate-400">适合直接粘贴或完整维护 .env。</div>
        </div>
      </div>
    </div>

    <template v-if="editMode === 'visual'">
      <div class="glass-card p-4">
        <div class="flex flex-wrap gap-2">
          <button
            v-for="item in visualCategories"
            :key="item.key"
            @click="visualCategory = item.key"
            class="pill-tab flex items-center gap-2"
            :class="visualCategory === item.key
              ? 'pill-tab-active'
              : ''"
          >
            <span class="text-base">{{ item.icon }}</span>
            {{ item.label }}
          </button>
        </div>
      </div>

      <div
        v-if="visualCategory === 'runtime'"
        class="glass-card p-6"
      >
        <div class="mb-6 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div class="mb-2 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300">
              <span>⚙️</span>
              Runtime Config
            </div>
            <h3 class="section-heading">运行配置</h3>
            <p class="section-subtitle">
              直接修改 CloudMail、CPA、代理和 API Key。保存后会写入 .env，并立即用于后续请求。
            </p>
          </div>
          <div class="flex items-center gap-3">
            <span v-if="runtimeSaved" class="status-badge border-emerald-400/20 bg-emerald-500/10 text-emerald-200">已保存</span>
            <span
              class="status-badge min-w-[84px] justify-center"
              :class="runtimeConfigured
                ? 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200'
                : 'border-white/10 bg-white/5 text-slate-400'"
            >
              {{ runtimeConfigured ? '已配置' : '未配置' }}
            </span>
          </div>
        </div>

        <div
          v-if="runtimeMessage"
          class="mb-4 rounded-2xl px-4 py-3 text-sm border"
          :class="runtimeMessageClass"
        >
          {{ runtimeMessage }}
        </div>

        <div v-if="runtimeLoading" class="text-sm text-slate-400">
          正在加载当前配置...
        </div>
        <div v-else class="space-y-4">
          <div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            <div v-for="field in runtimeFields" :key="field.key" class="rounded-2xl border border-white/10 bg-white/5 p-4">
              <label class="mb-2 block text-sm font-medium text-slate-300">
                {{ field.prompt }}
                <span v-if="!field.optional" class="text-red-400">*</span>
                <span v-if="field.key === 'API_KEY'" class="ml-1 text-xs text-slate-500">（留空自动生成）</span>
              </label>
              <input
                v-model="runtimeForm[field.key]"
                :type="fieldInputType(field.key)"
                :placeholder="field.default || ''"
                class="input-dark"
              />
            </div>
          </div>

          <div class="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/5 p-4 lg:flex-row lg:items-center lg:justify-between">
            <p class="text-xs leading-6 text-slate-400">
              已在执行中的任务不会回滚；新配置会用于之后的 CloudMail / CPA / 浏览器请求。
            </p>
            <button
              @click="saveRuntimeConfig"
              :disabled="runtimeSaving || runtimeLoading"
              class="btn-primary"
            >
              {{ runtimeSaving ? '保存中...' : '保存配置' }}
            </button>
          </div>
        </div>
      </div>

      <Settings
        v-else-if="visualCategory === 'admin'"
        :admin-status="adminStatus"
        :codex-status="codexStatus"
        section="admin"
        @refresh="$emit('refresh')"
        @admin-progress="$emit('admin-progress')"
      />

      <Settings
        v-else-if="visualCategory === 'auto-check'"
        :admin-status="adminStatus"
        :codex-status="codexStatus"
        section="auto-check"
        @refresh="$emit('refresh')"
        @admin-progress="$emit('admin-progress')"
      />
    </template>

    <div v-else class="glass-card space-y-4 p-6">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div class="mb-2 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300">
            <span>📝</span>
            Source Editor
          </div>
          <h3 class="section-heading">源文件编辑</h3>
          <p class="section-subtitle">
            直接编辑 .env 源文件。保存后会立即重载并校验 CloudMail / CPA 配置。
          </p>
        </div>
        <div class="status-badge break-all font-mono text-[11px] text-slate-400">
          {{ sourcePath || '.env' }}
        </div>
      </div>

      <div
        v-if="sourceMessage"
        class="rounded-2xl px-4 py-3 text-sm border"
        :class="sourceMessageClass"
      >
        {{ sourceMessage }}
      </div>

      <textarea
        v-model="sourceContent"
        rows="20"
        spellcheck="false"
        class="textarea-dark min-h-[420px] font-mono"
        placeholder="在这里编辑 .env 内容"
      />

      <div class="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/5 p-4 lg:flex-row lg:items-center lg:justify-between">
        <p class="text-xs leading-6 text-slate-400">
          这里是原始文本模式，适合你直接粘贴或手工维护完整 .env。
        </p>
        <div class="flex gap-2">
          <button
            @click="loadSourceConfig"
            :disabled="sourceLoading || sourceSaving"
            class="btn-secondary"
          >
            {{ sourceLoading ? '加载中...' : '重新读取' }}
          </button>
          <button
            @click="saveSourceConfig"
            :disabled="sourceLoading || sourceSaving"
            class="btn-primary"
          >
            {{ sourceSaving ? '保存中...' : '保存源文件' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, setApiKey } from '../api.js'
import Settings from './Settings.vue'

defineProps({
  adminStatus: {
    type: Object,
    default: null,
  },
  codexStatus: {
    type: Object,
    default: null,
  },
})

const emit = defineEmits(['refresh', 'admin-progress'])

const editModes = [
  { key: 'visual', label: '可视化编辑', icon: '⚙️' },
  { key: 'source', label: '源文件编辑', icon: '📝' },
]

const visualCategories = [
  { key: 'runtime', label: '运行配置', icon: '🧩' },
  { key: 'admin', label: '管理员 / 主号', icon: '🔐' },
  { key: 'auto-check', label: '巡检设置', icon: '🔄' },
]

const editMode = ref('visual')
const visualCategory = ref('runtime')

const runtimeFields = ref([])
const runtimeForm = reactive({})
const runtimeLoading = ref(false)
const runtimeSaving = ref(false)
const runtimeSaved = ref(false)
const runtimeMessage = ref('')
const runtimeMessageClass = ref('')

const sourcePath = ref('')
const sourceContent = ref('')
const sourceLoading = ref(false)
const sourceSaving = ref(false)
const sourceLoaded = ref(false)
const sourceMessage = ref('')
const sourceMessageClass = ref('')

const runtimeConfigured = computed(
  () => runtimeFields.value.length > 0 && runtimeFields.value.every(field => field.optional || field.configured),
)

function setRuntimeMessage(text, type = 'success') {
  runtimeMessage.value = text
  runtimeMessageClass.value = type === 'success'
    ? 'bg-green-500/10 text-green-400 border-green-500/20'
    : 'bg-red-500/10 text-red-400 border-red-500/20'
  window.clearTimeout(setRuntimeMessage._timer)
  setRuntimeMessage._timer = window.setTimeout(() => {
    runtimeMessage.value = ''
  }, 8000)
}

function setSourceMessage(text, type = 'success') {
  sourceMessage.value = text
  sourceMessageClass.value = type === 'success'
    ? 'bg-green-500/10 text-green-400 border-green-500/20'
    : 'bg-red-500/10 text-red-400 border-red-500/20'
  window.clearTimeout(setSourceMessage._timer)
  setSourceMessage._timer = window.setTimeout(() => {
    sourceMessage.value = ''
  }, 8000)
}

function fieldInputType(key) {
  return key.includes('PASSWORD') || key.includes('KEY') ? 'password' : 'text'
}

async function loadRuntimeConfig() {
  runtimeLoading.value = true
  try {
    const result = await api.getRuntimeConfig()
    runtimeFields.value = result.fields || []

    for (const key of Object.keys(runtimeForm)) {
      if (!runtimeFields.value.find(field => field.key === key)) {
        delete runtimeForm[key]
      }
    }
    for (const field of runtimeFields.value) {
      runtimeForm[field.key] = field.value ?? field.default ?? ''
    }
  } catch (e) {
    console.error('加载运行时配置失败:', e)
    setRuntimeMessage('加载运行时配置失败: ' + e.message, 'error')
  } finally {
    runtimeLoading.value = false
  }
}

async function saveRuntimeConfig() {
  runtimeSaving.value = true
  runtimeSaved.value = false
  try {
    const payload = {}
    for (const field of runtimeFields.value) {
      payload[field.key] = runtimeForm[field.key] ?? ''
    }
    const result = await api.saveRuntimeConfig(payload)
    if (result.api_key) {
      setApiKey(result.api_key)
    }
    setRuntimeMessage(result.message || '配置保存成功')
    runtimeSaved.value = true
    window.setTimeout(() => { runtimeSaved.value = false }, 3000)
    await loadRuntimeConfig()
    emit('refresh')
  } catch (e) {
    setRuntimeMessage(e.message, 'error')
  } finally {
    runtimeSaving.value = false
  }
}

async function loadSourceConfig() {
  sourceLoading.value = true
  try {
    const result = await api.getRuntimeConfigSource()
    sourcePath.value = result.path || '.env'
    sourceContent.value = result.content || ''
    sourceLoaded.value = true
  } catch (e) {
    console.error('加载源文件失败:', e)
    setSourceMessage('加载源文件失败: ' + e.message, 'error')
  } finally {
    sourceLoading.value = false
  }
}

async function saveSourceConfig() {
  sourceSaving.value = true
  try {
    const result = await api.saveRuntimeConfigSource({ content: sourceContent.value })
    if (result.api_key) {
      setApiKey(result.api_key)
    }
    setSourceMessage(result.message || '源文件保存成功')
    await Promise.all([loadSourceConfig(), loadRuntimeConfig()])
    emit('refresh')
  } catch (e) {
    setSourceMessage(e.message, 'error')
  } finally {
    sourceSaving.value = false
  }
}

watch(editMode, async (next) => {
  if (next === 'source' && !sourceLoaded.value) {
    await loadSourceConfig()
  }
})

onMounted(async () => {
  await loadRuntimeConfig()
})
</script>
