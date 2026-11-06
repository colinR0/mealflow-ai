import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

function sessionWith(respond, saved = '') {
  const source = readFileSync(new URL('../src/shared/session.ts', import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  })
  const storage = new Map(saved ? [['mealflow.session.v1', saved]] : [])
  const calls = []
  class ApiError extends Error { constructor(message, statusCode) { super(message); this.statusCode = statusCode } }
  const exports = {}
  runInNewContext(outputText, {
    exports, Error, ApiError,
    uni: {
      getStorageSync: (key) => storage.get(key) || '',
      setStorageSync: (key, value) => storage.set(key, value),
      removeStorageSync: (key) => storage.delete(key),
    },
    require: (name) => {
      if (name === 'vue') return { reactive: (value) => value }
      if (name === './transport') return {
        ApiError,
        request: async (...args) => { calls.push(args); return respond(...args) },
        requestLogin: async (...args) => { calls.push(args); return respond(...args) },
      }
      throw new Error(`Unexpected import: ${name}`)
    },
  })
  return { api: exports, storage, calls, ApiError }
}

test('first launch without a session stays signed out', async () => {
  const { api, calls } = sessionWith(() => { throw new Error('Unexpected request') })
  await api.restoreSession()
  assert.equal(api.session.restored, true)
  assert.equal(api.session.userId, '')
  assert.equal(calls.length, 0)
})

test('valid stored session restores the existing household', async () => {
  const saved = { userId: 'user-1', csrfToken: 'csrf', accessToken: 'token', householdId: 'home-1' }
  const { api, calls } = sessionWith((path) => {
    if (path === '/auth/me') return { user_id: 'user-1', phone_masked: '139****0000' }
    if (path === '/households') return [{ id: 'home-1', name: '我的家庭' }]
    throw new Error(path)
  }, saved)
  await api.restoreSession()
  assert.equal(api.session.householdId, 'home-1')
  assert.equal(api.session.phoneMasked, '139****0000')
  assert.deepEqual(calls.map(([path]) => path), ['/auth/me', '/households'])
})

test('login provisions a missing personal space once', async () => {
  let reads = 0
  const { api, calls } = sessionWith((path) => {
    if (path === '/auth/login') return { user_id: 'user-2', csrf_token: 'csrf' }
    if (path === '/auth/me') return { user_id: 'user-2', phone_masked: '138****1234' }
    if (path === '/households') return ++reads === 1 ? [] : [{ id: 'home-2', name: '我的家庭' }]
    if (path === '/households/personal/ensure') return { id: 'home-2' }
    throw new Error(path)
  })
  await api.login('13800001234', 'password')
  assert.equal(api.session.householdId, 'home-2')
  assert.equal(calls.filter(([path]) => path === '/households/personal/ensure').length, 1)
})

test('network failure preserves saved credentials for retry', async () => {
  const saved = { userId: 'user-3', csrfToken: 'csrf', accessToken: 'token', householdId: 'home-3' }
  const { api, storage } = sessionWith(() => { throw new Error('网络连接失败') }, saved)
  await api.restoreSession()
  assert.equal(api.session.restoreError, '网络连接失败')
  assert.equal(api.session.userId, 'user-3')
  assert.equal(storage.has('mealflow.session.v1'), true)
})

test('expired session clears credentials', async () => {
  const saved = { userId: 'user-4', csrfToken: 'csrf', accessToken: 'token', householdId: 'home-4' }
  const { api, storage, ApiError } = sessionWith(() => { throw new ApiError('登录已失效', 401) }, saved)
  await api.restoreSession()
  assert.equal(api.session.userId, '')
  assert.equal(storage.has('mealflow.session.v1'), false)
})

test('app launch routes signed-out and offline sessions to login, valid sessions home', async () => {
  const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
    .match(/<script lang="ts">([\s\S]*?)<\/script>/)[1]
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  })
  for (const [session, expected] of [
    [{ userId: '' }, '/pages/auth/login'],
    [{ userId: 'user-1', restoreError: '网络连接失败' }, '/pages/auth/login'],
    [{ userId: 'user-1', restoreError: '' }, '/pages/fridge/index'],
  ]) {
    const exports = {}
    const routes = []
    runInNewContext(outputText, {
      exports,
      uni: {
        reLaunch: ({ url }) => routes.push(url),
        switchTab: ({ url }) => routes.push(url),
      },
      require: (name) => {
        if (name === 'vue') return { defineComponent: (options) => options }
        if (name === './shared/session') return { session, restoreSession: async () => {} }
        throw new Error(name)
      },
    })
    exports.default.onLaunch()
    await Promise.resolve()
    assert.deepEqual(routes, [expected])
  }
})
