import { API_BASE } from './config'
import { session } from './session'
import { handleExpiredSession } from './transport'

export interface AgentEvent { schema_version: string; run_id: string; seq: number; type: string; payload: Record<string, unknown> }
interface ChunkedRequestTask extends UniApp.RequestTask { onChunkReceived(callback: (chunk: { data: ArrayBuffer }) => void): void }

export function streamRun(
  runId: string,
  cursor: number,
  onEvent: (event: AgentEvent) => void,
  onEnd: (cursor: number) => void,
): () => void {
  let lastSeq = cursor
  // H5 uni.request does not expose native chunk callbacks; fetch streams the same SSE API.
  // #ifdef H5
  const controller = new AbortController()
  void fetch(`${API_BASE}/agent/turn-runs/${runId}/events`, {
    credentials: 'include',
    headers: { Accept: 'text/event-stream', 'Last-Event-ID': `${runId}:${cursor}` },
    signal: controller.signal,
  }).then(async (response) => {
    if (response.status === 401) { handleExpiredSession(); return }
    if (!response.ok) throw new Error(`事件连接失败：${response.status}`)
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() || ''
      for (const frame of frames) {
        const data = frame.split(/\r?\n/).find((line) => line.startsWith('data: '))?.slice(6)
        if (!data) continue
        const event = JSON.parse(data) as AgentEvent
        if (event.seq <= lastSeq) continue
        lastSeq = event.seq
        onEvent(event)
      }
    }
    if (!controller.signal.aborted) onEnd(lastSeq)
  })
    // Connection or parsing failures resume through the page's persisted-run recovery.
    .catch(() => { if (!controller.signal.aborted) onEnd(lastSeq) })
  return () => controller.abort()
  // #endif
  // #ifndef H5
  let stopped = false
  const task = (uni.request as unknown as (options: UniApp.RequestOptions) => ChunkedRequestTask)({
    url: `${API_BASE}/agent/turn-runs/${runId}/events`,
    method: 'GET',
    enableChunked: true,
    responseType: 'text',
    header: {
      Accept: 'text/event-stream',
      ...(session.csrfToken ? { 'X-CSRF-Token': session.csrfToken } : {}),
      ...(session.accessToken ? { Authorization: `Bearer ${session.accessToken}` } : {}),
      'Last-Event-ID': `${runId}:${cursor}`,
    },
    success: (response) => {
      if (response.statusCode === 401) { handleExpiredSession(); return }
      if (!stopped) onEnd(lastSeq)
    },
    fail: () => { if (!stopped) onEnd(lastSeq) },
  } as UniApp.RequestOptions)
  let buffer = ''
  const decoder = new TextDecoder()
  task.onChunkReceived((chunk) => {
    if (stopped) return
    buffer += decoder.decode(chunk.data, { stream: true })
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() || ''
    for (const frame of frames) {
      const data = frame.split(/\r?\n/).find((line) => line.startsWith('data: '))?.slice(6)
      if (!data) continue
      const event = JSON.parse(data) as AgentEvent
      if (event.seq <= lastSeq) continue
      lastSeq = Math.max(lastSeq, event.seq)
      onEvent(event)
    }
  })
  return () => { stopped = true; task.abort() }
  // #endif
}
