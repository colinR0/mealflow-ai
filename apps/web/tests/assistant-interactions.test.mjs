import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

function loadPage(path, request, { uni = {}, modules = {} } = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8')
  const script = source.match(/<script lang="ts">([\s\S]*?)<\/script>/)[1]
  const { outputText } = ts.transpileModule(script, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  })
  const exports = {}
  runInNewContext(outputText, {
    exports, setTimeout, clearTimeout, setInterval, clearInterval, uni,
    require(name) {
      if (name in modules) return modules[name]
      if (name === 'vue') return { defineComponent: (options) => options }
      if (name.endsWith('/session')) return { session: { householdId: 'home' } }
      if (name.endsWith('/transport')) return { request }
      return {}
    },
  })
  const component = exports.default
  const page = component.data()
  for (const [key, method] of Object.entries(component.methods)) page[key] = method.bind(page)
  return { page, component, source }
}

test('closing a quick result prevents an old response reopening it', async () => {
  let finish
  const { page } = loadPage('../src/pages/assistant/index.vue', () => new Promise((resolve) => { finish = resolve }))
  page.input = '怎么焯水'
  const pending = page.quickAction('skills')
  page.closeQuickResult()
  finish({ text: '旧结果' })
  await pending
  assert.equal(page.quickResult, null)
  assert.equal(page.quickLoading, false)
})

test('empty restriction quick action asks for a query before calling the server', () => {
  let calls = 0
  const { page } = loadPage('../src/pages/assistant/index.vue', () => {
    calls++
    return Promise.resolve({ text: '不应调用' })
  })
  page.quickResult = { template: 'local', text: '旧结果' }
  page.input = '  '
  const result = page.quickAction('restrictions')
  assert.equal(result, undefined)
  assert.equal(calls, 0)
  assert.equal(page.quickResult, null)
  assert.equal(page.quickError, '请先输入菜名或食材，再检索。')
  assert.equal(page.quickLoading, false)
})

test('an old failure cannot replace a newer quick result', async () => {
  let fail
  let calls = 0
  const { page } = loadPage('../src/pages/assistant/index.vue', () => ++calls === 1
    ? new Promise((resolve, reject) => { fail = reject }) : Promise.resolve({ text: '新结果' }))
  page.input = '焯水'
  const old = page.quickAction('skills')
  page.closeQuickResult()
  await page.quickAction('skills')
  fail(new Error('旧请求失败'))
  await old
  assert.equal(page.quickResult.text, '新结果')
  assert.equal(page.quickError, '')
})

test('assistant strips citation tags without joining recipe lines', async () => {
  const { page } = loadPage('../src/pages/assistant/index.vue', () => Promise.resolve({}))
  assert.equal(
    page.cleanText('清蒸鱼 [recipe:abc:v1]\n1. 洗净\n来源：[recipe:abc:v1]\n2. 蒸熟'),
    '清蒸鱼\n1. 洗净\n2. 蒸熟',
  )
})

test('assistant does not render recipe source cards after inline steps', async () => {
  const { page } = loadPage('../src/pages/assistant/index.vue', () => Promise.resolve({}))
  const sources = page.recipeSources({
    citations: ['recipe:abc:v1', 'web:def'],
    evidence: [
      { id: 'recipe:abc:v1', kind: 'recipe', content: { steps: ['洗净', '蒸熟'] } },
      { id: 'web:def', kind: 'web', title: '网页摘要' },
    ],
  })
  assert.deepEqual(sources.map((item) => item.id), ['web:def'])
})

test('assistant previews a batch procurement draft with recipe amounts', () => {
  const { page } = loadPage('../src/pages/assistant/index.vue', () => Promise.resolve({}))
  const action = {
    id: 'a', type: 'add_procurement', payload_hash: '', version: 1,
    payload: {
      items: [
        { name: '五花肉', required: '150克', quantity_status: 'unknown' },
        { name: '芹菜', required: '1把', quantity_status: 'unknown' },
        { name: '番茄', required: '1kg', available: '0.5kg', quantity_status: 'unknown' },
      ],
    },
  }
  assert.equal(page.actionLines(action).join('|'), [
    '五花肉 · 菜谱所需 150克 · 数量待核对',
    '芹菜 · 菜谱所需 1把 · 数量待核对',
    '番茄 · 菜谱所需 1kg · 库存 0.5kg · 数量待核对',
  ].join('|'))
  assert.equal(page.actionTitle(action), '加入采购清单（3 项）')
  assert.equal(page.actionNote(action), '尚未保存；确认后加入采购清单（待购买），不会写入库存。')

  const legacy = {
    id: 'b', type: 'add_procurement', payload_hash: '', version: 1,
    payload: { name: '番茄', quantity_status: 'known', quantity: '2', unit: '个' },
  }
  assert.equal(page.actionLines(legacy).join('|'), '番茄 · 2 个')
  assert.equal(page.actionTitle(legacy), '加入采购清单')
})

test('assistant adds thinking steps and reveals the answer progressively', async () => {
  const { page } = loadPage('../src/pages/assistant/index.vue', () => Promise.resolve({}))
  page.scrollBottom = () => {}
  const run = {
    run_id: 'r', text: '', status: 'succeeded',
    result: { text: '你好 [recipe:abc:v1]', thinking_steps: ['读取家庭档案', '核对可核验来源'] },
    steps: [], expanded: false, visibleText: '', typing: false, typingTimer: null,
    error: '', deciding: false, requestKey: 'r',
  }
  page.present(run)
  assert.deepEqual(run.steps, ['读取家庭档案', '核对可核验来源'])
  assert.equal(run.expanded, true)
  assert.equal(run.typing, true)
  page.finishTyping(run)
  assert.equal(run.visibleText, '你好')

  run.visibleText = ''
  page.revealText(run, '你好', 0)
  await new Promise((resolve) => setTimeout(resolve, 120))
  assert.equal(run.visibleText, '你好')
  assert.equal(run.typing, false)
  assert.equal(run.typingTimer, null)
})

test('assistant sends a selected direction as a fresh turn', () => {
  const { page } = loadPage('../src/pages/assistant/index.vue', () => Promise.resolve({}))
  let submitted = ''
  page.send = () => { submitted = page.input; page.input = '' }
  const option = {
    key: 'light', label: '清淡蒸煮', example: '清蒸鱼',
    reason: '库存已有番茄', missing: [], prompt: '我对虾仁过敏，按「清淡蒸煮」方向推荐菜谱。',
  }
  page.chooseOption(option)
  assert.equal(submitted, option.prompt)
  page.busy = true
  submitted = ''
  page.input = '原输入'
  page.chooseOption(option)
  assert.equal(submitted, '')
  assert.equal(page.input, '原输入')
})

test('history decoration keeps pending assistant options', () => {
  const { page } = loadPage('../src/pages/assistant/index.vue', () => Promise.resolve({}))
  const option = { key: 'soup', label: '汤羹暖胃', example: '番茄汤', reason: '库存暂无记录', prompt: '按方向推荐' }
  const run = page.decorate({
    run_id: 'r', text: '我想吃虾了', status: 'succeeded',
    result: { text: '请选择方向', options: [option] },
    steps: [], expanded: false, visibleText: '', typing: false, typingTimer: null,
    error: '', deciding: false, requestKey: 'r',
  })
  assert.deepEqual(run.result.options, [option])
  assert.equal(run.typing, false)
})

test('selecting an ingredient suppresses an in-flight suggestion response', async () => {
  let finish
  const { page, component } = loadPage('../src/pages/fridge/manual-add.vue', () => new Promise((resolve) => { finish = resolve }))
  page.name = '番茄'
  const pending = page.searchDictionary('番茄')
  page.selected = { id: 'tomato', name: '番茄' }
  component.watch.name.call(page, '番茄')
  finish([{ id: 'other', name: '番茄酱' }])
  await pending
  assert.equal(page.suggestions.length, 0)
})

test('recipe detail prefills a clean dish request without ingredients', () => {
  let prompt = ''
  let target = ''
  const { page } = loadPage('../src/pages/shopping/recipes.vue', () => Promise.resolve({}), {
    uni: { switchTab: ({ url }) => { target = url } },
    modules: {
      '../../shared/assistantPrompt': { setAssistantPrompt: (text) => { prompt = text } },
    },
  })
  page.selectedRecipe = {
    name: '凉皮',
    servings: 2,
    duration_minutes: 30,
    ingredient_names: ['凉皮', '面筋', '黄瓜'],
  }
  page.askAssistant()
  assert.equal(
    prompt,
    '我想做凉皮。请结合我家现有库存，告诉我是否够用，还缺什么，并给我具体做法。',
  )
  assert.equal(target, '/pages/assistant/index')
})
