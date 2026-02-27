import axios, { type AxiosRequestConfig } from 'axios'
import { useAuthStore } from '../stores/authStore'

const BASE_URL = '/api/v1'

const api = axios.create({
  baseURL: BASE_URL,
  timeout: 30000,
})

// 请求拦截器
api.interceptors.request.use(
  (config) => {
    const token = useAuthStore.getState().token
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (error.code === 'ECONNABORTED') {
      error.message = '请求超时，请检查网络或稍后重试'
    } else if (error.message === 'Network Error' || !error.response) {
      error.message = '网络异常，请检查连接后重试'
    } else if (error.response?.data?.detail) {
      error.message = typeof error.response.data.detail === 'string'
        ? error.response.data.detail
        : error.message
    }
    const isAuthRequest =
      error.config?.url === '/auth/login' || error.config?.url === '/auth/register'
    if (error.response?.status === 401 && !isAuthRequest) {
      useAuthStore.getState().logout()
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

/** 统一带鉴权的 fetch，用于流式、下载等无法用 axios 的场景；401 时与 axios 一致：登出并跳转登录 */
export async function fetchWithAuth(
  input: string | URL,
  init?: RequestInit
): Promise<Response> {
  const token = useAuthStore.getState().token
  const url = typeof input === 'string' && !input.startsWith('http') ? `${input.startsWith('/') ? '' : BASE_URL + '/'}${input}` : input
  const res = await fetch(url, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    } as HeadersInit,
  })
  if (res.status === 401) {
    const isAuthUrl = String(input).includes('/auth/login') || String(input).includes('/auth/register')
    if (!isAuthUrl) {
      useAuthStore.getState().logout()
      window.location.href = '/login'
    }
  }
  return res
}

/** 封装流式 POST，返回 response 与 body 的 reader（仅可消费一次）；调用方需自行解析 SSE。 */
export async function streamPost(
  path: string,
  body: unknown
): Promise<{ response: Response; reader: ReadableStreamDefaultReader<Uint8Array> }> {
  const url = path.startsWith('/') ? path : `${BASE_URL}/${path}`
  const res = await fetchWithAuth(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail || res.statusText)
  }
  const reader = res.body?.getReader()
  if (!reader) throw new Error('无响应体')
  return { response: res, reader }
}

export default api
