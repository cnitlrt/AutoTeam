import { computed, ref } from 'vue'

const STORAGE_KEY = 'autoteam_theme'
const theme = ref('dark')

let initialized = false

function applyTheme(value) {
  if (typeof document === 'undefined') return
  const normalized = value === 'light' ? 'light' : 'dark'
  document.documentElement.dataset.theme = normalized
  document.documentElement.style.colorScheme = normalized
}

export function initTheme() {
  if (initialized) return
  initialized = true

  if (typeof window !== 'undefined') {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    theme.value = stored === 'light' || stored === 'dark' ? stored : 'dark'
  }

  applyTheme(theme.value)
}

export function setTheme(value) {
  theme.value = value === 'light' ? 'light' : 'dark'
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(STORAGE_KEY, theme.value)
  }
  applyTheme(theme.value)
}

export function toggleTheme() {
  setTheme(theme.value === 'dark' ? 'light' : 'dark')
}

export function useTheme() {
  initTheme()
  return {
    theme,
    isDark: computed(() => theme.value === 'dark'),
    setTheme,
    toggleTheme,
  }
}
