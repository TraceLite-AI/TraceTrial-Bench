import { createInterface } from 'node:readline'

const sdkModule = process.env.HARNESSBENCH_DEEPSEEK_SDK_MODULE
  ?? 'file:///opt/deepseek-runtime/node_modules/@deepseek-ai/dsh-sdk-client/lib/index.js'
const { DeepSeekHarness } = await import(sdkModule)
const STARTUP_SETTLE_MS = 250

let harness
let closing = false

function finishReason(events) {
  for (let index = events.length - 1; index >= 0; index--) {
    const event = events[index]
    if (event?.type === 'turn/end') return event.data?.reason
  }
  return undefined
}

function finalResponse(events) {
  for (let index = events.length - 1; index >= 0; index--) {
    const event = events[index]
    if (event?.type !== 'assistant/message') continue
    const content = event.data?.message?.content
    if (!Array.isArray(content)) continue
    const text = content
      .filter(block => block?.type === 'text' && typeof block.text === 'string')
      .map(block => block.text)
      .join('')
    if (text) return text
  }
  return ''
}

function inboxReceiptMatches(notification, sessionId, messageId) {
  if (notification?.method !== 'session.event') return false
  if (notification.params?.sessionId !== sessionId) return false
  const event = notification.params?.event
  if (event?.type !== 'agent/inbox/spliced') return false
  const inserted = event.data?.inserted
  return Array.isArray(inserted) && inserted.some(message => message?.id === messageId)
}

function settlementChildId(notification, sessionId) {
  if (notification?.method !== 'session.event') return undefined
  if (notification.params?.sessionId !== sessionId) return undefined
  const event = notification.params?.event
  if (event?.type !== 'user/message') return undefined
  const source = event.data?.source
  if (source?.kind !== 'subagent-settled' || source?.form !== 'notice') return undefined
  return typeof source.senderSessionId === 'string' ? source.senderSessionId : undefined
}

async function runThroughContinuableSettlement(params, onNotification) {
  const client = harness.client
  const subscription = client.subscribeSessionTree(params.sessionId)
  const events = []
  const notifications = []
  const discoveredChildren = new Set()
  const activeChildren = new Set()
  const settledChildren = new Set()
  let rootIdle = false
  try {
    const messageId = await client.prompt(
      params.sessionId,
      [{ type: 'text', text: params.input }],
    )
    let received = false
    while (true) {
      const notification = await subscription.next()
      if (!received) {
        if (!inboxReceiptMatches(notification, params.sessionId, messageId)) continue
        received = true
      }
      notifications.push(notification)
      onNotification(notification)
      if (
        notification.method === 'session.event'
        && notification.params?.sessionId === params.sessionId
      ) {
        events.push(notification.params.event)
      }
      if (notification.method === 'subagent.started') {
        const childId = notification.params?.childSessionId
        if (
          notification.params?.parentSessionId === params.sessionId
          && typeof childId === 'string'
          && childId
        ) {
          discoveredChildren.add(childId)
          activeChildren.add(childId)
        }
      } else if (notification.method === 'subagent.finished') {
        const childId = notification.params?.childSessionId
        if (typeof childId === 'string' && discoveredChildren.has(childId)) {
          activeChildren.delete(childId)
        }
      }
      const settledChild = settlementChildId(notification, params.sessionId)
      if (settledChild !== undefined) {
        settledChildren.add(settledChild)
        // The notice is a waking parent message. Its durable session event is
        // emitted before the Agent publishes the ensuing running transition,
        // so the previous idle must no longer be considered terminal here.
        rootIdle = false
      }

      if (
        notification.method === 'session.status'
        && notification.params?.sessionId === params.sessionId
      ) {
        rootIdle = notification.params?.status === 'idle'
        if (rootIdle && discoveredChildren.size === 0) break
      }
      if (
        rootIdle
        && discoveredChildren.size > 0
        && activeChildren.size === 0
        && [...discoveredChildren].every(childId => settledChildren.has(childId))
      ) {
        break
      }
    }
  } finally {
    subscription.close()
  }
  return {
    sessionId: params.sessionId,
    finalResponse: finalResponse(events),
    finishReason: finishReason(events),
    events,
    notifications,
  }
}

function errorPayload(error) {
  return {
    name: error instanceof Error ? error.name : 'Error',
    message: error instanceof Error ? error.message : String(error),
    ...error && typeof error === 'object' && typeof error.code === 'string'
      ? { code: error.code }
      : {},
  }
}

async function dispatch(message) {
  if (!message || typeof message !== 'object' || !Number.isSafeInteger(message.id)) {
    throw new Error('bridge request requires an integer id')
  }
  if (message.method === 'initialize') {
    if (harness !== undefined) throw new Error('bridge is already initialized')
    const params = message.params
    if (!params || typeof params !== 'object') throw new Error('initialize params are missing')
    harness = new DeepSeekHarness({
      launch: {
        command: params.runtimeCommand,
        args: params.runtimeArgs,
        cwd: params.cwd,
        env: params.env,
        shutdownTimeoutMs: 3000,
        disposeEofGraceMs: 6000,
        disposeGraceMs: 3000,
      },
      cwd: params.cwd,
      provider: params.provider,
      model: params.model,
      ...Number.isSafeInteger(params.maxTokens) && params.maxTokens > 0
        ? { maxTokens: params.maxTokens }
        : {},
    })
    // The SDK transport can finish its initialize handshake while sibling
    // Cordis plugins are completing async startup. Start here, rather than on
    // the first prompt, and give local MCP discovery one bounded quiescence
    // window so the first model request sees the configured tool catalog.
    await harness.start()
    await new Promise(resolve => setTimeout(resolve, STARTUP_SETTLE_MS))
    return { initialized: true }
  }
  if (message.method === 'run') {
    if (harness === undefined) throw new Error('bridge is not initialized')
    const params = message.params
    if (!params || typeof params.input !== 'string' || typeof params.sessionId !== 'string') {
      throw new Error('run params are invalid')
    }
    const onNotification = notification => respond({
        id: message.id,
        event: 'notification',
        notification,
      })
    if (params.waitForContinuableChildren === true) {
      return runThroughContinuableSettlement(params, onNotification)
    }
    const result = await harness.run(params.input, {
      sessionId: params.sessionId,
      onNotification,
    })
    return {
      sessionId: result.sessionId,
      finalResponse: result.finalResponse,
      finishReason: finishReason(result.events),
      events: result.events,
      notifications: result.notifications,
    }
  }
  if (message.method === 'close') {
    closing = true
    if (harness !== undefined) await harness.close()
    return { closed: true }
  }
  throw new Error(`unknown bridge method: ${String(message.method)}`)
}

function respond(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`)
}

const input = createInterface({ input: process.stdin, crlfDelay: Infinity })
let queue = Promise.resolve()
input.on('line', (line) => {
  queue = queue.then(async () => {
    let message
    try {
      message = JSON.parse(line)
      const result = await dispatch(message)
      respond({ id: message.id, ok: true, result })
      if (closing) {
        input.close()
        process.exitCode = 0
      }
    } catch (error) {
      respond({
        id: Number.isSafeInteger(message?.id) ? message.id : null,
        ok: false,
        error: errorPayload(error),
      })
    }
  })
})

input.on('close', () => {
  queue = queue.finally(async () => {
    if (!closing && harness !== undefined) {
      try {
        await harness.close()
      } catch (error) {
        process.stderr.write(`DeepSeek bridge shutdown failed: ${errorPayload(error).message}\n`)
        process.exitCode = 1
      }
    }
  })
})
