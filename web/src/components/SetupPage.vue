<template>
  <div class="relative min-h-screen overflow-hidden">
    <div class="pointer-events-none absolute inset-0">
      <div class="absolute left-[-6rem] top-[-6rem] h-72 w-72 rounded-full bg-blue-500/20 blur-3xl"></div>
      <div class="absolute bottom-[-8rem] right-[-4rem] h-80 w-80 rounded-full bg-cyan-500/15 blur-3xl"></div>
    </div>

    <div class="relative mx-auto flex min-h-screen max-w-6xl items-center px-4 py-10">
      <div class="grid w-full items-center gap-8 lg:grid-cols-[1.05fr_0.95fr]">
        <div class="hidden lg:block">
          <div class="mb-4 inline-flex items-center gap-2 rounded-full border border-blue-400/20 bg-blue-500/10 px-4 py-2 text-sm text-blue-200">
            <span class="inline-block h-2 w-2 rounded-full bg-cyan-400 shadow-[0_0_16px_rgba(34,211,238,0.9)]"></span>
            初始引导
          </div>
          <h1 class="max-w-2xl text-5xl font-bold leading-tight text-white">
            先设置
            <span class="bg-gradient-to-r from-blue-300 via-cyan-300 to-sky-400 bg-clip-text text-transparent">API Key</span>
            ，再进入面板继续配置
          </h1>
          <p class="mt-5 max-w-2xl text-lg leading-8 text-slate-300">
            现在启动阶段只强制要求 API Key。CloudMail、CPA / Sub2API、代理等运行项都可以在登录后去配置面板里慢慢填。
          </p>

          <div class="mt-8 grid max-w-2xl grid-cols-3 gap-4">
            <div class="glass-card-soft p-4">
              <div class="text-2xl">🔐</div>
              <div class="mt-3 text-sm font-medium text-white">安全优先</div>
              <div class="mt-1 text-xs leading-5 text-slate-400">没有 API Key 不允许进入系统</div>
            </div>
            <div class="glass-card-soft p-4">
              <div class="text-2xl">🧩</div>
              <div class="mt-3 text-sm font-medium text-white">配置集中</div>
              <div class="mt-1 text-xs leading-5 text-slate-400">登录后在配置面板统一编辑</div>
            </div>
            <div class="glass-card-soft p-4">
              <div class="text-2xl">✨</div>
              <div class="mt-3 text-sm font-medium text-white">立即生效</div>
              <div class="mt-1 text-xs leading-5 text-slate-400">保存后直接应用新配置</div>
            </div>
          </div>
        </div>

        <div class="glass-card w-full p-7 sm:p-8">
          <div class="mb-6 flex items-center gap-3">
            <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500/30 to-cyan-500/20 text-2xl shadow-inner shadow-white/10">
              🔑
            </div>
            <div>
              <h2 class="text-2xl font-semibold text-white">AutoTeam 初始配置</h2>
              <p class="mt-1 text-sm text-slate-400">完成这一项后即可进入控制面板</p>
            </div>
          </div>

          <div v-if="message" class="mb-4 rounded-2xl px-4 py-3 text-sm border" :class="messageClass">
            {{ message }}
          </div>

          <div class="space-y-4">
            <div v-for="field in fields" :key="field.key" class="rounded-2xl border border-white/10 bg-white/5 p-4">
              <label class="mb-2 block text-sm font-medium text-slate-300">
                {{ field.prompt }}
                <span v-if="!field.optional" class="text-red-400">*</span>
                <span v-if="field.key === 'API_KEY'" class="ml-1 text-xs text-slate-500">（留空自动生成）</span>
              </label>
              <input
                v-model="form[field.key]"
                :type="field.key.includes('PASSWORD') || field.key.includes('KEY') ? 'password' : 'text'"
                :placeholder="field.default || ''"
                class="input-dark"
              />
            </div>
          </div>

          <button @click="save" :disabled="saving" class="btn-primary mt-6 w-full py-3">
            {{ saving ? '保存中...' : '保存并进入面板' }}
          </button>

          <div class="mt-4 rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-xs leading-6 text-slate-400">
            保存后你可以继续去配置面板补充 CloudMail、CPA / Sub2API、代理以及巡检参数。
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { api, setApiKey } from '../api.js'

const emit = defineEmits(['configured'])

const fields = ref([])
const form = reactive({})
const saving = ref(false)
const message = ref('')
const messageClass = ref('')

onMounted(async () => {
  try {
    const result = await api.getSetupStatus()
    fields.value = result.fields
    for (const f of result.fields) {
      form[f.key] = f.default || ''
    }
  } catch (e) {
    message.value = '获取配置状态失败: ' + e.message
    messageClass.value = 'bg-red-500/10 text-red-400 border-red-500/20'
  }
})

async function save() {
  saving.value = true
  message.value = ''
  try {
    const result = await api.saveSetup({ ...form })
    if (result.api_key) {
      setApiKey(result.api_key)
    }
    message.value = result.message
    messageClass.value = 'bg-green-500/10 text-green-400 border-green-500/20'
    setTimeout(() => emit('configured'), 1000)
  } catch (e) {
    message.value = e.message
    messageClass.value = 'bg-red-500/10 text-red-400 border-red-500/20'
  } finally {
    saving.value = false
  }
}
</script>
