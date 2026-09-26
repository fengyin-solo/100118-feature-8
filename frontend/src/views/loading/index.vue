<template>
  <section class="page" data-module="loading">
    <header class="page-head">
      <div>
        <h2>装卸任务管理</h2>
        <p class="page-desc">维护装卸任务，围绕任务编号、关联航次、作业类型、计划箱量做登记、筛选与状态流转；支持勾选多条任务批量开工、批量复核。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记装卸任务</button>
        <button class="btn" type="button" @click="exportRows">导出装卸任务清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="batch-bar">
      <label class="batch-check">
        <input
          type="checkbox"
          :checked="allVisibleSelected"
          :disabled="!rows.length"
          @change="toggleSelectAll"
        />
        全选本页
      </label>
      <span class="batch-count">已选 {{ selectedIds.size }} 条</span>
      <button
        v-for="batchAction in batchActions"
        :key="batchAction"
        class="btn primary"
        type="button"
        :disabled="!selectedIds.size || batchLoading"
        @click="runBatch(batchAction)"
      >
        批量{{ batchAction }}
      </button>
      <button class="btn ghost" type="button" :disabled="!selectedIds.size" @click="selectedIds.clear()">
        清空勾选
      </button>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th class="check-col">选择</th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td class="check-col">
            <input
              type="checkbox"
              :checked="selectedIds.has(Number(row.id))"
              @change="toggleOne(Number(row.id))"
            />
          </td>
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无装卸任务数据，可先登记装卸任务</td>
        </tr>
      </tbody>
    </table>

    <section v-if="lastBatch" class="batch-panel">
      <header class="batch-panel-head">
        <div>
          <strong>批量{{ lastBatch.action }}回执</strong>
          <span class="batch-summary">{{ lastBatch.message }}</span>
        </div>
        <button class="btn ghost" type="button" @click="clearBatchResult">关闭回执</button>
      </header>
      <ul class="batch-list">
        <li v-for="item in lastBatch.results" :key="item.id" class="batch-item">
          <span class="tag" :class="outcomeClass(item.outcome)">{{ outcomeLabel(item.outcome) }}</span>
          <span class="batch-task">{{ taskLabel(item) }}</span>
          <span class="batch-msg">{{ item.message }}</span>
          <span class="batch-state">当前状态：{{ currentState(item) }}</span>
        </li>
      </ul>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条装卸任务记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type Outcome = 'applied' | 'duplicate' | 'blocked'

interface BatchItem {
  id: number
  task_no: string | null
  outcome: Outcome
  ok: boolean
  duplicate: boolean
  message: string
  entry: Row | null
}

interface BatchResult {
  action: string
  total: number
  applied: number
  duplicated: number
  blocked: number
  message: string
  results: BatchItem[]
  at: number
}

const ENDPOINT = '/api/loading'
const BATCH_STORAGE_KEY = 'loading:last-batch'
const columns = ["任务编号", "关联航次", "作业类型", "计划箱量", "完成箱量", "作业班组", "开始时间", "任务状态"]
const actions = ["确认开工", "提交复核", "确认完成"]
const batchActions = ["确认开工", "提交复核"]
const stats = [{"label": "待开工任务", "value": 0}, {"label": "作业中任务", "value": 0}, {"label": "今日完成箱量", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const selectedIds = ref(new Set<number>())
const batchLoading = ref(false)
const lastBatch = ref<BatchResult | null>(null)

const allVisibleSelected = computed(
  () => rows.value.length > 0 && rows.value.every(row => selectedIds.value.has(Number(row.id))),
)

function toggleOne(id: number) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  selectedIds.value = next
}

function toggleSelectAll(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  const next = new Set(selectedIds.value)
  for (const row of rows.value) {
    const id = Number(row.id)
    if (checked) {
      next.add(id)
    } else {
      next.delete(id)
    }
  }
  selectedIds.value = next
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '装卸任务登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('装卸任务动作未生效，请稍后重试')
    }
    const payload = await response.json()
    if (payload.ok === false) {
      errorMessage.value = payload.message || '装卸任务动作未生效'
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '装卸任务操作失败'
  }
}

async function runBatch(action: string) {
  if (!selectedIds.value.size || batchLoading.value) {
    return
  }
  errorMessage.value = ''
  batchLoading.value = true
  try {
    const response = await request(`${ENDPOINT}/batch/actions`, {
      method: 'POST',
      body: JSON.stringify({ action, entry_ids: [...selectedIds.value] }),
    })
    if (!response.ok) {
      const detail = await response.json().catch(() => null)
      throw new Error(detail?.detail || '批量操作未生效，请稍后重试')
    }
    const payload = (await response.json()) as Omit<BatchResult, 'at'>
    lastBatch.value = { ...payload, at: Date.now() }
    persistBatch()
    selectedIds.value = new Set()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量操作失败'
  } finally {
    batchLoading.value = false
  }
}

function persistBatch() {
  if (!lastBatch.value) {
    sessionStorage.removeItem(BATCH_STORAGE_KEY)
    return
  }
  sessionStorage.setItem(BATCH_STORAGE_KEY, JSON.stringify(lastBatch.value))
}

function restoreBatch() {
  const raw = sessionStorage.getItem(BATCH_STORAGE_KEY)
  if (!raw) {
    return
  }
  try {
    lastBatch.value = JSON.parse(raw) as BatchResult
  } catch {
    sessionStorage.removeItem(BATCH_STORAGE_KEY)
  }
}

function clearBatchResult() {
  lastBatch.value = null
  sessionStorage.removeItem(BATCH_STORAGE_KEY)
}

function taskLabel(item: BatchItem): string {
  return item.task_no || String(item.entry?.['任务编号'] ?? `任务 #${item.id}`)
}

function currentState(item: BatchItem): string {
  const state = item.entry?.['任务状态'] ?? item.entry?.status
  return state === null || state === undefined || state === '' ? '—' : String(state)
}

function outcomeLabel(outcome: Outcome): string {
  return outcome === 'applied' ? '已处理' : outcome === 'duplicate' ? '重复提交' : '已拦下'
}

function outcomeClass(outcome: Outcome): string {
  return outcome === 'applied' ? 'tag-ok' : outcome === 'duplicate' ? 'tag-dup' : 'tag-block'
}

/** 刷新后拿列表/明细对账：回执里的任务快照与当前详情保持一致，缘由文字仍保留。 */
async function reconcileBatch() {
  if (!lastBatch.value) {
    return
  }
  await Promise.all(
    lastBatch.value.results.map(async item => {
      const visible = rows.value.find(row => Number(row.id) === item.id)
      if (visible) {
        item.entry = visible
        return
      }
      try {
        const response = await request(`${ENDPOINT}/${item.id}`)
        if (response.ok) {
          item.entry = (await response.json()) as Row
        } else {
          item.entry = null
        }
      } catch {
        item.entry = null
      }
    }),
  )
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams({
    ...(filters.value as Record<string, string>),
    size: '200',
  }).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('装卸任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await reconcileBatch()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '装卸任务列表读取失败'
  }
}

onMounted(() => {
  restoreBatch()
  void reload()
})
</script>
