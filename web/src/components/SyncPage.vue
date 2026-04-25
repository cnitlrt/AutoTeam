<template>
  <div>
    <h2 class="text-xl font-bold text-white mb-2">同步中心</h2>
    <p class="text-sm text-gray-400 mb-6">
      这里放对账与同步类操作：本地账号池对账、同步到已启用远端，以及从 CPA 反向拉取认证文件。
    </p>
    <TaskPanel
      mode="sync"
      :running-task="runningTask"
      :admin-status="adminStatus"
      @task-started="$emit('task-started')"
      @refresh="$emit('refresh')"
    />
  </div>
</template>

<script setup>
import TaskPanel from './TaskPanel.vue'

defineProps({
  runningTask: Object,
  adminStatus: Object,
})

defineEmits(['task-started', 'refresh'])
</script>
