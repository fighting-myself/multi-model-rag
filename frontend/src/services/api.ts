import axios, { type AxiosInstance, type AxiosRequestConfig } from 'axios'
import { useAuthStore } from '../stores/authStore'
import type { MultiAgentRunRequest, MultiAgentSsePayload } from '../types/api'

/** 与下方响应拦截器一致：成功时 resolve 为 response.data，而非 AxiosResponse */
export type ApiInstance = Omit<
  AxiosInstance,
  'request' | 'get' | 'delete' | 'head' | 'options' | 'post' | 'put' | 'patch'
> & {
  request<T = unknown>(config: AxiosRequestConfig): Promise<T>
  get<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T>
  delete<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T>
  head<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T>
  options<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T>
  post<T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T>
  put<T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T>
  patch<T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T>
}

/**
 * 开发时默认走 Vite 代理 `/api` -> 后端。
 * 若界面提示「网络异常」且代理异常，可在 frontend/.env 或 frontend/.env.development 中设置：
 *   VITE_API_BASE_URL=http://127.0.0.1:8000
 * 将直连后端 API（需后端 CORS 允许；当前默认可用 *）。
 */
function normalizeApiBase(): string {
  const raw = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() ?? ''
  if (!raw) return '/api/v1'
  const base = raw.replace(/\/$/, '')
  return base.endsWith('/api/v1') ? base : `${base}/api/v1`
}

const BASE_URL = normalizeApiBase()

/**
 * 将路径拼成完整请求 URL（供 fetch 使用）。
 * 若以 `/api` 开头则视为站点内 API 绝对路径，原样返回，避免与 BASE 拼接成 `/api/v1/api/v1/...`。
 */
export function resolveApiUrl(path: string): string {
  if (path.startsWith('/api')) {
    return path
  }
  const p = path.replace(/^\//, '')
  const base = BASE_URL.endsWith('/') ? BASE_URL.slice(0, -1) : BASE_URL
  return `${base}/${p}`
}

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
    const url = String(error.config?.url ?? '')
    const isAuthRequest = url.includes('/auth/login') || url.includes('/auth/register')
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
  let url: string | URL
  if (typeof input === 'string') {
    if (input.startsWith('http')) {
      url = input
    } else if (input.startsWith('/api')) {
      url = input
    } else {
      url = resolveApiUrl(input)
    }
  } else {
    url = input
  }
  const res = await fetch(url, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    } as HeadersInit,
  })
  if (res.status === 401) {
    const isAuthUrl =
      String(input).includes('/auth/login') || String(input).includes('/auth/register')
    if (!isAuthUrl) {
      useAuthStore.getState().logout()
      window.location.href = '/login'
    }
  }
  return res
}

/** 封装流式 POST（JSON body），返回 response 与 body 的 reader；支持 signal 用于停止对话。 */
export async function streamPost(
  path: string,
  body: unknown,
  options?: { signal?: AbortSignal; headers?: Record<string, string> }
): Promise<{ response: Response; reader: ReadableStreamDefaultReader<Uint8Array> }> {
  const url = resolveApiUrl(path)
  const res = await fetchWithAuth(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(options?.headers ?? {}),
    },
    body: JSON.stringify(body),
    signal: options?.signal,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail || res.statusText)
  }
  const reader = res.body?.getReader()
  if (!reader) throw new Error('无响应体')
  return { response: res, reader }
}

/**
 * 多智能体 SSE：解析 `data: {...}\\n\\n`，按事件顺序回调。
 */
export async function consumeMultiAgentRunStream(
  payload: MultiAgentRunRequest,
  options: { signal?: AbortSignal; onEvent: (e: MultiAgentSsePayload) => void }
): Promise<void> {
  const { reader } = await streamPost('/multi-agent/run/stream', payload, {
    signal: options.signal,
    headers: { Accept: 'text/event-stream' },
  })
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split(/\r?\n\r?\n/)
    buf = parts.pop() ?? ''
    for (const block of parts) {
      const line = block.split(/\r?\n/).find((l) => l.startsWith('data:'))
      if (!line) continue
      const json = line.replace(/^data:\s?/, '').trim()
      if (!json) continue
      let evt: MultiAgentSsePayload
      try {
        evt = JSON.parse(json) as MultiAgentSsePayload
      } catch {
        continue
      }
      options.onEvent(evt)
    }
  }
}

/** 智能问答：先上传文件，返回 upload_id */
export async function uploadChatFile(file: File): Promise<{ upload_id: string; file_name: string; type: string }> {
  const url = resolveApiUrl('chat/attachments/upload')
  const formData = new FormData()
  formData.append('file', file, file.name)
  const res = await fetchWithAuth(url, { method: 'POST', body: formData })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail || res.statusText)
  }
  return res.json()
}

export default api as ApiInstance
