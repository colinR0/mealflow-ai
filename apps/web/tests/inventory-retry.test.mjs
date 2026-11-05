import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

// Execute the actual page methods with transport and uni-app boundaries stubbed.
function pageWith(request) {
  const source = readFileSync(new URL('../src/pages/fridge/inventory.vue', import.meta.url), 'utf8')
    .match(/<script lang="ts">([\s\S]*?)<\/script>/)[1]
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  })
  const exports = {}
  const session = { householdId: 'household-1' }
  runInNewContext(outputText, {
    exports, Error, setTimeout, clearTimeout,
    uni: { showModal: ({ success }) => success({ confirm: true }) },
    require: (name) => {
      if (name === 'vue') return { defineComponent: (options) => options }
      if (name.endsWith('/session')) return { session }
      if (name.endsWith('/transport')) return { request }
      if (name.endsWith('.vue')) return { default: {} }
      throw new Error(`Unexpected import: ${name}`)
    },
  })
  const component = exports.default
  const page = component.data()
  for (const [key, method] of Object.entries(component.methods)) page[key] = method.bind(page)
  Object.defineProperty(page, 'editCanSave', { get: () => component.computed.editCanSave.call(page) })
  page.items = [{ id: 'lot-1', name: 'Food', version: 1, quantity_status: 'unknown', notes: '' }]
  page.offset = 30; page.total = 60; page.hasMore = true
  page.search = 'Food'; page.filter = 'unknown'; page.status = 'active'
  return page
}

function freshPage() {
  return { items: [{ id: 'lot-1', name: 'Food', version: 2 }], total: 1, offset: 0, has_more: false }
}

for (const action of ['consume', 'remove', 'edit']) {
  test(`${action} failure retries from offset zero and replaces stale versions`, async () => {
    const calls = []
    const page = pageWith(async (url, payload, ...args) => {
      calls.push({ url, payload, args })
      if (payload) throw new Error('Response lost after commit')
      return freshPage()
    })
    if (action === 'edit') {
      page.edit(page.items[0])
      await page.saveEdit()
      assert.equal(page.editError, 'Response lost after commit')
    } else await page.transition(page.items[0], action)
    assert.equal(page.errorRetry, 'refresh')
    assert.equal(page.busyId, '')
    assert.equal(page.offset, 30)
    await page.retryError()
    assert.equal(calls.length, 2)
    assert.match(calls[1].url, /offset=0&limit=30&status=active&quantity_status=unknown&search=Food/)
    assert.equal(page.items.length, 1)
    assert.equal(page.items[0].version, 2)
    assert.equal(page.editing, null)
    assert.equal(page.error, '')
  })
}

test('failed pagination retries the same offset and keeps previously loaded records', async () => {
  const urls = []
  const page = pageWith(async (url) => {
    urls.push(url)
    if (urls.length === 1) throw new Error('Page unavailable')
    return { items: [{ id: 'lot-2', version: 1 }], total: 31, offset: 30, has_more: false }
  })
  await page.load()
  assert.equal(page.errorRetry, 'page')
  assert.equal(page.offset, 30)
  assert.equal(page.items.length, 1)
  await page.retryError()
  assert.equal(urls[0], urls[1])
  assert.match(urls[1], /offset=30&/)
  assert.equal(page.items.length, 2)
  assert.equal(page.items[0].id, 'lot-1')
  assert.equal(page.offset, 31)
})

test('legacy array response renders inventory without attempting duplicate pagination', async () => {
  const page = pageWith(async () => [{ id: 'legacy-lot', name: 'Food', version: 1 }])
  await page.load()
  assert.equal(page.items.length, 2)
  assert.equal(page.items[1].id, 'legacy-lot')
  assert.equal(page.total, 1)
  assert.equal(page.offset, 1)
  assert.equal(page.hasMore, false)
  assert.equal(page.error, '')
})

test('malformed inventory response shows a readable backend contract error', async () => {
  const page = pageWith(async () => ({ results: [] }))
  await page.load()
  assert.match(page.error, /库存接口响应格式无效/)
  assert.equal(page.items.length, 1)
})

test('edit sends the original decimal text without Number rounding', async () => {
  let submitted
  const page = pageWith(async (_url, payload) => {
    if (payload) submitted = payload
    return freshPage()
  })
  page.edit(page.items[0]); page.editStatus = 'known'
  page.editQuantity = '999999999.99900001'; page.editUnit = 'kg'
  await page.saveEdit()
  assert.equal(submitted.quantity, '999999999.99900001')
})
