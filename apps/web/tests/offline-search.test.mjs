import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

function loadModule() {
  const source = readFileSync(new URL('../src/shared/offlineSearch.ts', import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  })
  const exports = {}
  runInNewContext(outputText, { exports, JSON, Array, Set, String, Number, Object, Math })
  return exports
}

const bundle = {
  data_version: 'test-v1',
  recipes: [{
    id: 'recipe-1',
    name: '番茄炒蛋',
    servings: 2,
    duration_minutes: 15,
    category: '家常',
    tags: ['快手'],
    taste_tags: [],
    allergens: ['鸡蛋'],
    steps: ['番茄切块', '炒熟'],
    source_name: '测试菜谱库',
    ingredients: [
      { name: '番茄', quantity: null, unit: null },
      { name: '鸡蛋', quantity: null, unit: null },
    ],
    search_tokens: ['番茄', '炒蛋', '鸡蛋', '家常'],
  }],
  skills: [{
    id: 'skill-1',
    title: '焯水',
    document_type: 'cooking_skill',
    content_version: 1,
    source_name: '测试技巧库',
    text: '冷水下锅。',
    search_tokens: ['焯水', '冷水', '下锅'],
  }],
}

test('offline lexical search matches space-separated and CJK bigram queries', () => {
  const { searchOffline } = loadModule()
  assert.equal(searchOffline(bundle, '番茄 鸡蛋').recipes[0].item.id, 'recipe-1')
  assert.equal(searchOffline(bundle, '番茄鸡蛋').recipes[0].item.id, 'recipe-1')
  assert.equal(searchOffline(bundle, '焯水').skills[0].item.id, 'skill-1')
  assert.equal(searchOffline(bundle, '不存在').recipes.length, 0)
  assert.equal(searchOffline(bundle, '   ').recipes.length, 0)
})

test('offline formatting never invents an amount', () => {
  const { formatOfflineRecipe } = loadModule()
  const text = formatOfflineRecipe(bundle.recipes[0], {
    dataVersion: 'test-v1',
    note: '离线词法检索：未执行服务端忌口校验。',
  })
  assert.match(text, /番茄、鸡蛋/)
  assert.doesNotMatch(text, /番茄 0/)
  assert.match(text, /离线数据版本：test-v1/)
})

test('bundle parsing rejects malformed stored data', () => {
  const { parseOfflineBundle } = loadModule()
  assert.equal(parseOfflineBundle(null), null)
  assert.equal(parseOfflineBundle({ recipes: [] }), null)
  assert.equal(parseOfflineBundle(bundle)?.data_version, 'test-v1')
})