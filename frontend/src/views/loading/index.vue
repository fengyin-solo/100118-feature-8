<template>
  <section class="page" data-module="loading">
    <header class="page-head">
      <div>
        <h2>装卸任务管理</h2>
        <p class="page-desc">维护装卸任务，围绕任务编号、关联航次、作业类型、计划箱量做登记、筛选与状态流转；支持勾选多条批量开工、批量复核。</p>
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
      <button class="btn primary" type="button" :disabled="!selectedIds.size || submitting" @click="runBatch('确认开工')">
        批量确认开工{{ selectedIds.size ? `（${selectedIds.size}）` : '' }}
      </button>
      <button class="btn primary" type="button" :disabled="!selectedIds.size || submitting" @click="runBatch('提交复核')">
        批量提交复核{{ selectedIds.size ? `（${selectedIds.size}）` : '' }}
      </button>
      <span class="batch-hint">勾选多条任务一次提交；个别任务被拦下不影响其它任务，回执会逐条说明缘由。</span>
    </div>

    <div v-if="batchResult" class="receipt-panel">
      <div class="receipt-head">
        <strong>{{ batchResult.action }}批量回执</strong>
        <span :class="['receipt-summary', batchResult.blocked ? 'has-blocked' : 'all-clear']">
          共 {{ batchResult.total }} 条：成功 {{ batchResult.updated }}，重复跳过 {{ batchResult.skipped }}，拦截 {{ batchResult.blocked }}
        </span>
        <button class="link" type="button" @click="batchResult = null">关闭回执</button>
      </div>
      <table class="receipt-table">
        <thead>
          <tr><th>任务编号</th><th>结果</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in batchResult.results" :key="item.id" :class="`receipt-${item.kind}`">
            <td>{{ item.taskNo ?? `#${item.id}` }}</td>
            <td>{{ kindLabel(item.kind) }}</td>
            <td>{{ item.message }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th class="col-check"><input type="checkbox" :checked="allChecked" @change="toggleAll" /></th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-blocked': blockedIds.has(Number(row.id)) }">
          <td class="col-check"><input type="checkbox" :checked="selectedIds.has(Number(row.id))" @change="toggleOne(Number(row.id))" /></td>
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

    <footer class="page-foot">
      <span>共 {{ total }} 条装卸任务记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type ReceiptKind = 'updated' | 'noop' | 'rejected'

interface ReceiptItem {
  id: number
  taskNo: string | null
  ok: boolean
  kind: ReceiptKind
  message: string
}

interface BatchResult {
  ok: boolean
  action: string
  total: number
  updated: number
  skipped: number
  blocked: number
  message: string
  results: ReceiptItem[]
}

const ENDPOINT = '/api/loading'
const columns = ["任务编号", "关联航次", "作业类型", "计划箱量", "完成箱量", "作业班组", "开始时间", "任务状态"]
const actions = ["确认开工", "提交复核", "确认完成"]
const statuses = ["待开工", "作业中", "待复核", "已完成"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const selectedIds = ref<Set<number>>(new Set())
const submitting = ref(false)
const batchResult = ref<BatchResult | null>(null)

const stats = computed(() => [
  { label: "待开工任务", value: rows.value.filter(row => row.status === '待开工').length },
  { label: "作业中任务", value: rows.value.filter(row => row.status === '作业中').length },
  {
    label: "在管完成箱量",
    value: rows.value.reduce((sum, row) => sum + (Number(row['完成箱量']) || 0), 0),
  },
])

const blockedIds = computed(() => new Set(
  (batchResult.value?.results ?? [])
    .filter(item => item.kind === 'rejected')
    .map(item => item.id),
))

const allChecked = computed(() =>
  rows.value.length > 0 && rows.value.every(row => selectedIds.value.has(Number(row.id))),
)

const KIND_LABELS: Record<ReceiptKind, string> = {
  updated: '成功',
  noop: '重复跳过',
  rejected: '拦截',
}

function kindLabel(kind: ReceiptKind) {
  return KIND_LABELS[kind]
}

function toggleOne(id: number) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  selectedIds.value = next
}

function toggleAll() {
  if (allChecked.value) {
    selectedIds.value = new Set()
    return
  }
  selectedIds.value = new Set(rows.value.map(row => Number(row.id)))
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
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!payload.ok) {
      errorMessage.value = payload.message
      return
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '装卸任务操作失败'
  }
}

async function runBatch(action: string) {
  if (!selectedIds.value.size || submitting.value) {
    return
  }
  errorMessage.value = ''
  submitting.value = true
  try {
    const ids = [...selectedIds.value]
    const response = await request(`${ENDPOINT}/batch-actions`, {
      method: 'POST',
      body: JSON.stringify({ action, items: ids.map(id => ({ id })) }),
    })
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as { detail?: string } | null
      throw new Error(payload?.detail ?? '批量操作未生效，请稍后重试')
    }
    batchResult.value = (await response.json()) as BatchResult
    selectedIds.value = new Set()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量操作失败'
  } finally {
    submitting.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams({
    keyword: filters.value['任务编号'] ?? '',
    status: filters.value['任务状态'] ?? '',
  }).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('装卸任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    // 翻页或筛选后剔除已不在列表里的勾选项。
    const visible = new Set(rows.value.map(row => Number(row.id)))
    selectedIds.value = new Set([...selectedIds.value].filter(id => visible.has(id)))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '装卸任务列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.batch-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.batch-bar .btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.batch-hint {
  color: var(--muted);
  font-size: 12px;
}
.col-check {
  width: 36px;
  text-align: center;
}
.col-check input {
  cursor: pointer;
}
.row-blocked td {
  background: #fef3f2;
}
.receipt-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-left: 3px solid var(--brand);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.receipt-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  font-size: 13px;
}
.receipt-head .link {
  margin-left: auto;
}
.receipt-summary {
  font-size: 12px;
}
.receipt-summary.has-blocked {
  color: #b42318;
}
.receipt-summary.all-clear {
  color: #067647;
}
.receipt-table {
  width: 100%;
  border-collapse: collapse;
}
.receipt-table th,
.receipt-table td {
  border: 1px solid var(--border);
  padding: 6px 8px;
  font-size: 12px;
  text-align: left;
}
.receipt-rejected td {
  background: #fef3f2;
  color: #b42318;
}
.receipt-noop td {
  color: var(--muted);
}
</style>
