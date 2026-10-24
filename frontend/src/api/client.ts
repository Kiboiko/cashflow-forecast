/** Тонкий клиент к /api/v1: токен из localStorage, русские сообщения об ошибках, сигнал о выходе при 401. */

const TOKEN_KEY = 'kassa.token'
export const UNAUTHORIZED_EVENT = 'kassa:unauthorized'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* приватный режим: работаем без сохранения входа */
  }
}

function describe(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string' && detail) return detail
  if (status === 422) return 'Проверьте введённые данные.'
  if (status === 429) return 'Слишком много попыток. Подождите минуту.'
  if (status === 404) return 'Не найдено.'
  if (status >= 500) return 'Что-то пошло не так на сервере. Попробуйте ещё раз.'
  return 'Не получилось выполнить запрос.'
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (typeof init.body === 'string') headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, { ...init, headers })
  } catch {
    throw new ApiError(0, 'Нет связи с сервером. Проверьте интернет.')
  }
  if (response.status === 401 && path !== '/auth/login') {
    setToken(null)
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
  }
  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      /* тело не JSON */
    }
    throw new ApiError(response.status, describe(response.status, body))
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const json = (body: unknown): string | undefined => (body === undefined ? undefined : JSON.stringify(body))

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body: json(body) }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body: json(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  upload: <T>(path: string, form: FormData) => request<T>(path, { method: 'POST', body: form }),
}
