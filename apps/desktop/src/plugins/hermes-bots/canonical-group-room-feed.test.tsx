import { useStore } from '@nanostores/react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

// The room view follows the room log the way tla/RoomFeed.tla models it: it reads only after its cursor,
// applies a page only from cursor+1 (the view stays a prefix of the log), refreshes on groups.changed, and
// keeps a slow poll because a push can be lost.
const request = vi.hoisted(() => vi.fn())
const listeners = vi.hoisted(() => new Map<string, Set<(event: Record<string, unknown>) => void>>())
vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock, createGroupGateway } = await import('./group-test-utils')
  const gateway = createGroupGateway()
  const { en } = await import('@/i18n/en')
  const { CANONICAL_GROUP_LOCALES } = await import('./canonical-group-locales')
  const { ConfirmDialog } = await import('@/components/ui/confirm-dialog')

  const onEvent = (type: string, listener: (event: Record<string, unknown>) => void) => {
    const set = listeners.get(type) ?? new Set()
    set.add(listener)
    listeners.set(type, set)

    return () => set.delete(listener)
  }

  return { ...await pluginSdkMock(gateway.host), atom, useValue: useStore, ConfirmDialog,
    useQuery: (await import('@tanstack/react-query')).useQuery,
    useI18n: () => ({ t: en }),
    usePluginI18n: () => (key: string) => CANONICAL_GROUP_LOCALES.en[key.replace('canonical.', '') as keyof typeof CANONICAL_GROUP_LOCALES.en] ?? key,
    Button: (p: ComponentProps<'button'>) => <button {...p} />,
    Input: (p: ComponentProps<'input'>) => <input {...p} />,
    RowButton: (p: ComponentProps<'button'>) => <button type="button" {...p} />,
    host: { ...gateway.host, requestProfile: request, onEvent } }
})

import { CanonicalGroupWorkspace, roomLogPageAfter } from './canonical-group-workspace'

const binding = { connectionId: 'local', profile: 'team', roomId: 'feed', roomName: 'Room' }
const message = (seq: number, text = `m${seq}`) => ({ seq, kind: 'message.user', actor: { kind: 'user', id: 'u' }, payload: { text } })

/** A scripted room: `log` is the server's log, groups.log pages it after since_seq like read_events. */
function serveRoom(room: { log: ReturnType<typeof message>[]; changeEvents?: boolean; pageSize?: number }) {
  request.mockImplementation(async (_route, method, params: { since_seq?: number; limit?: number }) => {
    if (method === 'groups.state') {
      return { room: { name: 'Room', members: [], latest_seq: room.log.at(-1)?.seq ?? 0 }, driver_status: {},
        ...(room.changeEvents ? { change_events: true } : {}) }
    }

    if (method === 'groups.log') {
      const since = params.since_seq ?? 0
      const after = room.log.filter(event => event.seq > since)
      const events = after.slice(0, room.pageSize ?? 100)

      return { events, has_more: events.length < after.length }
    }

    return {}
  })
}

const logReads = () => request.mock.calls.filter(call => call[1] === 'groups.log').map(call => call[2].since_seq)
const emit = (type: string, event: Record<string, unknown> = {}) => act(() => { for (const listener of listeners.get(type) ?? []) {listener({ type, ...event })} })

beforeEach(() => { Object.defineProperty(window, 'hermesDesktop', { configurable: true, writable: true, value: undefined }) })
afterEach(() => { cleanup(); request.mockReset(); listeners.clear(); localStorage.clear(); vi.useRealTimers() })

it('applies a page only from cursor+1, skipping events the view already holds', () => {
  const log = [message(1), message(2)]
  expect(roomLogPageAfter(2, [message(3), message(4)])?.map(event => event.seq)).toEqual([3, 4])
  expect(roomLogPageAfter(2, [message(2), message(3)])?.map(event => event.seq)).toEqual([3])
  expect(roomLogPageAfter(2, [])).toEqual([])
  expect(roomLogPageAfter(2, [message(4)])).toBeNull()
  expect(roomLogPageAfter(2, [message(3), message(5)])).toBeNull()
})

it('reads only what is new after the first refresh, across pages', async () => {
  const room = { log: [message(1), message(2), message(3)], changeEvents: true, pageSize: 2 }
  serveRoom(room)
  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText('m3')
  expect(logReads()).toEqual([0, 2])

  room.log.push(message(4))
  emit('groups.changed', { connectionId: 'local' })
  await screen.findByText('m4')
  expect(logReads()).toEqual([0, 2, 3])
  expect(screen.getAllByText(/^m\d$/).map(node => node.textContent)).toEqual(['m1', 'm2', 'm3', 'm4'])
})

it('refuses a page that would leave a hole and keeps the view it has', async () => {
  const room = { log: [message(1)], changeEvents: true }
  serveRoom(room)
  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText('m1')

  room.log.push(message(3))  // seq 2 is missing: applying it would show a log the room never had
  emit('groups.changed')
  expect((await screen.findByRole('alert')).textContent).toContain('Invalid room log cursor')
  expect(screen.queryByText('m3')).toBeNull()
  expect(screen.getByText('m1')).toBeTruthy()
})

it('refreshes on its own route’s and room’s groups.changed, not another connection’s or room’s', async () => {
  const room = { log: [message(1)], changeEvents: true }
  serveRoom(room)
  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText('m1')
  room.log.push(message(2))

  // A refresh issues groups.state synchronously, so an ignored signal is visible at once: no read starts.
  const stateReads = () => request.mock.calls.filter(call => call[1] === 'groups.state').length
  const reads = stateReads()
  emit('groups.changed', { connectionId: 'other-machine' })
  emit('groups.changed', { connectionId: 'local', payload: { room_ids: ['another-room'] } })
  expect(stateReads()).toBe(reads)

  emit('groups.changed', { connectionId: 'local', payload: { room_ids: ['another-room', 'feed'] } })
  await screen.findByText('m2')
  room.log.push(message(3))
  emit('groups.changed', { connectionId: 'local', payload: {} })  // process-wide: every view re-reads
  await screen.findByText('m3')
})

it('keeps the 2 s poll on a backend without change events, and a slow backstop on one with them', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const legacy = { log: [message(1)] }
  serveRoom(legacy)
  const view = render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText('m1')
  legacy.log.push(message(2))
  await act(() => vi.advanceTimersByTimeAsync(2100))
  await screen.findByText('m2')
  view.unmount()

  request.mockReset()
  const pushed = { log: [message(1)], changeEvents: true }
  serveRoom(pushed)
  render(<CanonicalGroupWorkspace binding={{ ...binding, roomId: 'pushed' }} />)
  await screen.findByText('m1')
  pushed.log.push(message(2))
  await act(() => vi.advanceTimersByTimeAsync(2100))
  expect(screen.queryByText('m2')).toBeNull()
  // A lost push still lands: the backstop poll reads the delta.
  await act(() => vi.advanceTimersByTimeAsync(15_000))
  await screen.findByText('m2')
})

it('a signal that lands during a read runs one more read after it', async () => {
  const room = { log: [message(1)], changeEvents: true }

  let release: () => void = () => {}
  serveRoom(room)
  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText('m1')

  const serve = request.getMockImplementation()!
  // Hold the read's log answer (already computed without m2) until the second signal has landed.
  request.mockImplementation(async (route, method, params) => {
    const answer = await serve(route, method, params)

    if (method === 'groups.log') {await new Promise<void>(resolve => { release = resolve })}

    return answer
  })
  emit('groups.changed')
  await waitFor(() => expect(logReads().length).toBe(2))
  room.log.push(message(2))
  emit('groups.changed')  // mid-read: must not be dropped
  request.mockImplementation(serve)
  release()
  await screen.findByText('m2')
})

it('rebuilds from the start when the room log is shorter than the view (re-hosted)', async () => {
  const room = { log: [message(1), message(2), message(3)], changeEvents: true }
  serveRoom(room)
  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText('m3')

  room.log = [message(1, 'fresh-1')]
  emit('groups.changed')
  await screen.findByText('fresh-1')
  expect(screen.queryByText('m3')).toBeNull()
  expect(logReads().at(-1)).toBe(0)
})


it('runs one room read at a time for every caller: Refresh joins a push read in flight', async () => {
  const room = { log: [message(1)], changeEvents: true }
  serveRoom(room)
  const serve = request.getMockImplementation()!
  let failLog = true
  request.mockImplementation(async (...args: Parameters<typeof serve>) =>
    args[1] === 'groups.log' && failLog ? Promise.reject(new Error('busy store')) : serve(...args))
  render(<CanonicalGroupWorkspace binding={binding} />)
  const retry = await screen.findByRole('button', { name: 'Refresh' })  // the read-error banner's button

  let release!: () => void
  const held = new Promise<void>(resolve => { release = resolve })
  let inFlight = 0
  let maxInFlight = 0
  failLog = false
  request.mockImplementation(async (...args: Parameters<typeof serve>) => {
    if (args[1] !== 'groups.state') {return serve(...args)}
    inFlight++
    maxInFlight = Math.max(maxInFlight, inFlight)

    try { await held

      return await serve(...args) } finally { inFlight-- }
  })
  room.log.push(message(2))
  emit('groups.changed', { connectionId: 'local' })   // a push read starts and is held
  fireEvent.click(retry)                              // the user's Refresh lands mid-read
  await act(async () => { release() })
  await screen.findByText('m2')
  expect(maxInFlight).toBe(1)
})

it('keeps the same log array when a read brings nothing new', async () => {
  const room = { log: [message(1), message(2)], changeEvents: true }
  serveRoom(room)
  const seen: unknown[] = []
  const History = await import('./canonical-group-history')
  const render0 = vi.spyOn(History, 'CanonicalGroupHistory')
  render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText('m2')

  for (const call of render0.mock.calls) {seen.push((call[0] as { events: unknown }).events)}
  const before = seen.at(-1)
  emit('groups.changed', { connectionId: 'local', payload: { room_ids: ['feed'] } })
  await waitFor(() => expect(logReads().length).toBeGreaterThanOrEqual(2))
  await act(async () => { await Promise.resolve() })
  expect((render0.mock.calls.at(-1)?.[0] as { events: unknown }).events).toBe(before)
  render0.mockRestore()
})

it('holds the room route\'s socket open while the view is shown, so its pushes arrive', async () => {
  serveRoom({ log: [message(1)], changeEvents: true })
  const sdk = await import('@hermes/plugin-sdk')
  const released = vi.fn()
  const retain = vi.fn(() => released)
  Object.assign(sdk.host, { retainProfileSocket: retain })
  const view = render(<CanonicalGroupWorkspace binding={binding} />)
  await screen.findByText('m1')
  expect(retain).toHaveBeenCalledWith({ connectionId: 'local', profile: 'team', targetProfile: 'team', mode: 'local' })
  expect(released).not.toHaveBeenCalled()
  view.unmount()
  expect(released).toHaveBeenCalledTimes(1)
  delete (sdk.host as { retainProfileSocket?: unknown }).retainProfileSocket
})

it('refuses a page that says there is more but moves the cursor nowhere, instead of spinning or stopping short', async () => {
  request.mockImplementation(async (_route, method) => method === 'groups.state'
    ? { room: { name: 'Room', members: [], latest_seq: 5 }, driver_status: {}, change_events: true }
    : method === 'groups.log' ? { events: [], has_more: true } : {})
  render(<CanonicalGroupWorkspace binding={binding} />)
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(logReads()).toEqual([0])
})
