import { lemmaClient } from '../lemma-client'

export type Row = Record<string, any>

const client = lemmaClient

export async function sql<T = Row>(query: string): Promise<T[]> {
  const res: any = await client.datastore.query(query)
  const items = res?.items ?? res?.data?.items ?? res?.rows ?? []
  return items as T[]
}

export async function listRows<T = Row>(table: string, opts: {
  filters?: { field: string; op?: string; value: any }[]
  sort?: { field: string; direction?: 'asc' | 'desc' }[]
  limit?: number
} = {}): Promise<T[]> {
  const res: any = await client.records.list(table, {
    filters: (opts.filters ?? []) as any,
    sort: (opts.sort ?? []) as any,
    limit: opts.limit ?? 200,
  })
  return (res?.items ?? []) as T[]
}

export async function getRow(table: string, id: string): Promise<Row> {
  return (await client.records.get(table, id)) as Row
}

export async function updateRow(table: string, id: string, data: Row): Promise<Row> {
  return (await client.records.update(table, id, data)) as Row
}

export type FunctionOutcome = { ok: boolean; output: any; error?: string; runId?: string }

/** Run a pod function and normalise its response. Server-side policy checks live there. */
export async function runFn(name: string, input: Row = {}): Promise<FunctionOutcome> {
  try {
    const res: any = await client.functions.run(name, { input })
    const status = String(res?.status ?? '')
    const output = res?.output_data ?? res?.outputData ?? null
    const error = res?.error ?? null
    if (error || (status && !['COMPLETED', 'SUCCEEDED', 'RUNNING', 'QUEUED'].includes(status))) {
      return { ok: false, output, error: String(error || `Function finished with status ${status}`), runId: res?.id }
    }
    return { ok: true, output, runId: res?.id }
  } catch (e: any) {
    return { ok: false, output: null, error: extractError(e) }
  }
}

export function extractError(e: any): string {
  const raw = e?.message ?? String(e)
  const m = /\[(\d+)\]\s*([A-Z_]+):\s*([^\n(]+)/.exec(raw)
  if (m) return m[3].trim()
  return raw.replace(/\(request_id=[^)]+\)/, '').trim()
}

export async function uploadFile(file: File, directoryPath: string): Promise<Row> {
  const res: any = await client.files.upload(file, { directoryPath, name: file.name, searchEnabled: true })
  return res as Row
}

/** Perceptual hash + dominant colours, computed in the browser for the uploaded image.
 *  The same 8x8 average-hash convention the workspace seeds use, so hashes are comparable. */
export async function computeImageMeta(file: File): Promise<{ width: number; height: number; ahash: string; colors: string[] }> {
  const bitmap: any = await (window as any).createImageBitmap(file)
  const canvas = document.createElement('canvas')
  canvas.width = 8
  canvas.height = 8
  const ctx = canvas.getContext('2d')!
  ctx.drawImage(bitmap, 0, 0, 8, 8)
  const { data } = ctx.getImageData(0, 0, 8, 8)
  const grey: number[] = []
  for (let i = 0; i < data.length; i += 4) {
    grey.push(0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2])
  }
  const avg = grey.reduce((a, b) => a + b, 0) / grey.length
  const bits = grey.map((v) => (v >= avg ? '1' : '0')).join('')
  let hash = ''
  for (let i = 0; i < 64; i += 16) hash += parseInt(bits.slice(i, i + 16), 2).toString(16).padStart(4, '0')
  const small = document.createElement('canvas')
  small.width = 32
  small.height = 32
  const sctx = small.getContext('2d')!
  sctx.drawImage(bitmap, 0, 0, 32, 32)
  const sd = sctx.getImageData(0, 0, 32, 32).data
  const buckets = new Map<string, number>()
  for (let i = 0; i < sd.length; i += 4) {
    const key = `${Math.floor(sd[i] / 32) * 32},${Math.floor(sd[i + 1] / 32) * 32},${Math.floor(sd[i + 2] / 32) * 32}`
    buckets.set(key, (buckets.get(key) ?? 0) + 1)
  }
  const colors = [...buckets.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k]) => {
    const [r, g, b] = k.split(',').map(Number)
    return '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')
  })
  const width = bitmap.width ?? 0
  const height = bitmap.height ?? 0
  if (bitmap.close) bitmap.close()
  return { width, height, ahash: hash, colors }
}

/** Watch one table live (WebSocket). Returns a disposer. */
export function watchTable(table: string, onChange: () => void): () => void {
  try {
    const handle = (client.datastore as any).watchChanges({ table, onChange: () => onChange() })
    return () => handle?.close?.()
  } catch {
    return () => {}
  }
}
