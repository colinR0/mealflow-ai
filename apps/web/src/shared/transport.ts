import { API_BASE } from './config'
import { session } from './session'

export class ApiError extends Error {
  constructor(message: string, readonly statusCode: number) { super(message) }
}

export function requestLogin<T>(path: string, data: unknown): Promise<T> {
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE}${path}`,
      method: 'POST',
      data: data as UniApp.RequestOptions['data'],
      header: {
        'Content-Type': 'application/json',
        // #ifdef APP-PLUS
        'X-MealFlow-Client': 'native-app',
        // #endif
      },
      withCredentials: true,
      success: (response) => {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new ApiError('手机号或密码不正确', response.statusCode))
          return
        }
        resolve(response.data as T)
      },
      fail: () => reject(new ApiError('网络连接失败，请检查服务地址和网络', 0)),
    })
  })
}

export function request<T>(path: string, data?: unknown, authenticated = true, extraHeaders: Record<string, string> = {}, method?: 'GET' | 'POST' | 'DELETE'): Promise<T> {
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE}${path}`,
      method: method || (data === undefined ? 'GET' : 'POST'),
      data: data as UniApp.RequestOptions['data'],
      header: {
        'Content-Type': 'application/json',
        ...(authenticated && session.csrfToken ? { 'X-CSRF-Token': session.csrfToken } : {}),
        ...(authenticated && session.accessToken ? { Authorization: `Bearer ${session.accessToken}` } : {}),
        ...extraHeaders,
      },
      withCredentials: true,
      success: (response) => {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          if (response.statusCode === 401) {
            session.userId = ''
            session.csrfToken = ''
            session.accessToken = ''
            session.householdId = ''
            session.households = []
            uni.removeStorageSync('mealflow.session.v1')
          }
          if (response.statusCode === 401) {
            session.userId = ''
            session.csrfToken = ''
            session.accessToken = ''
            session.householdId = ''
            session.households = []
            uni.removeStorageSync('mealflow.session.v1')
          }
          const code = (response.data as { detail?: { code?: string } })?.detail?.code
          const codes: Record<string, string> = { RUN_BUSY: '对话仍在执行，请先停止或等待完成', SESSION_NOT_FOUND: '对话已不存在，请新建对话', ACTION_STALE: '操作内容已变化，请刷新对话后核对', ACTION_EXPIRED: '操作已过期，请重新发起', HOUSEHOLD_RUN_LIMIT: '家庭任务正在处理中，请稍后再试', QUEUE_FULL: '服务繁忙，请稍后再试' }
          const messages: Record<number, string> = { 401: '登录已失效，请重新登录', 403: '无法访问当前家庭，请重新选择家庭', 404: '记录已不存在，请刷新后重试', 409: '记录已变化，请刷新后重试', 422: '填写内容有误，请检查输入' }
          reject(new ApiError((code && codes[code]) || messages[response.statusCode] || '请求未完成，请稍后重试', response.statusCode))
          return
        }
        resolve(response.data as T)
      },
      fail: () => reject(new ApiError('网络连接失败，请检查服务地址和网络', 0)),
    })
  })
}

export function patchRequest<T>(path: string, data: unknown): Promise<T> {
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE}${path}`,
      method: 'PATCH' as UniApp.RequestOptions['method'],
      data: data as UniApp.RequestOptions['data'],
      header: {
        'Content-Type': 'application/json',
        ...(session.csrfToken ? { 'X-CSRF-Token': session.csrfToken } : {}),
        ...(session.accessToken ? { Authorization: `Bearer ${session.accessToken}` } : {}),
      },
      withCredentials: true,
      success: (response) => {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          const messages: Record<number, string> = { 401: '登录已失效，请重新登录', 403: '无法访问当前家庭，请重新选择家庭', 404: '记录已不存在，请刷新后重试', 409: '档案已被更新，请刷新后重新编辑', 422: '填写内容有误，请检查姓名和日期范围' }
          reject(new ApiError(messages[response.statusCode] || '请求未完成，请稍后重试', response.statusCode))
          return
        }
        resolve(response.data as T)
      },
      fail: () => reject(new ApiError('网络连接失败，请检查服务地址和网络', 0)),
    })
  })
}
