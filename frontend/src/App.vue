<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'

type Player = { playerId: string; name: string }
type Point = { x: number; y: number }

type WsInMessage =
  | {
      type: 'welcome'
      roomId: string
      playerId: string
      protocolVersion?: number
      phase: 'lobby' | 'playing' | 'finished'
      boardSize: number
      turn: string | null
      hostId?: string | null
      turnMode?: 'host' | 'client' | 'random'
      winner: string | null
      ready: string[]
      colors: Record<string, string>
      players: Player[]
    }
  | {
      type: 'player_joined'
      roomId: string
      player: Player
      colors?: Record<string, string>
      hostId?: string | null
      turnMode?: 'host' | 'client' | 'random'
    }
  | {
      type: 'player_left'
      roomId: string
      playerId: string
      protocolVersion?: number
      hostId?: string | null
      turnMode?: 'host' | 'client' | 'random'
      turn?: string | null
    }
  | { type: 'player_ready'; roomId: string; playerId: string; ready: string[]; protocolVersion?: number }
  | { type: 'colors_update'; roomId: string; protocolVersion?: number; colors: Record<string, string> }
  | {
      type: 'turn_mode_update'
      roomId: string
      protocolVersion?: number
      hostId: string | null
      turnMode: 'host' | 'client' | 'random'
    }
  | {
      type: 'game_start'
      roomId: string
      protocolVersion?: number
      phase: 'playing'
      turn: string | null
      hostId?: string | null
      turnMode?: 'host' | 'client' | 'random'
      boardSize: number
      colors: Record<string, string>
      shapes: Record<string, Point[]>
    }
  | {
      type: 'move_applied'
      roomId: string
      protocolVersion?: number
      playerId: string
      x: number
      y: number
      phase: 'playing' | 'finished'
      turn: string | null
      winner: string | null
      winCells: Point[]
    }
  | { type: 'chat'; roomId: string; from: string; text: string }
  | { type: 'pong' }
  | { type: 'error'; message: string }

const form = reactive({
  serverHost: window.location.hostname || '127.0.0.1',
  serverPort: '8000',
  roomId: 'room1',
  playerId: `p${Math.floor(Math.random() * 100000)}`,
  name: 'player',
  color: '#111111',
  isHost: window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost'
})

const wsUrl = computed(() => {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const port = form.serverPort.trim()
  const hostPort = port ? `${form.serverHost}:${port}` : form.serverHost
  return `${proto}://${hostPort}/ws/${encodeURIComponent(form.roomId)}`
})

const status = ref<'disconnected' | 'connecting' | 'connected'>('disconnected')
const wsRef = ref<WebSocket | null>(null)

const myPlayerId = ref<string>('')
const players = ref<Player[]>([])
const phase = ref<'lobby' | 'playing' | 'finished'>('lobby')
const turn = ref<string | null>(null)
const winner = ref<string | null>(null)
const ready = ref<Set<string>>(new Set())
const boardSize = ref<number>(15)

const hostId = ref<string | null>(null)
const turnMode = ref<'host' | 'client' | 'random'>('random')

const shapes = ref<Record<string, Point[]>>({})
const colors = ref<Record<string, string>>({})
const winCells = ref<Set<string>>(new Set())
const serverProtocolVersion = ref<number | null>(null)

const logLines = ref<string[]>([])
function log(line: string) {
  const ts = new Date().toISOString().slice(11, 19)
  logLines.value.push(`[${ts}] ${line}`)
  if (logLines.value.length > 400) logLines.value.splice(0, logLines.value.length - 400)
}

const board = ref<string[][]>([]) // store owner playerId
function initBoard(size: number) {
  board.value = Array.from({ length: size }, () => Array.from({ length: size }, () => ''))
  winCells.value = new Set()
}

const cells = computed(() => {
  const out: Array<{ x: number; y: number; cell: string }> = []
  for (let y = 0; y < board.value.length; y++) {
    const row = board.value[y]
    for (let x = 0; x < row.length; x++) {
      out.push({ x, y, cell: row[x] })
    }
  }
  return out
})

function colorFor(pid: string) {
  return colors.value[pid] || (pid === myPlayerId.value ? form.color : '#888888')
}

function sendJson(obj: Record<string, unknown>) {
  const ws = wsRef.value
  if (!ws || ws.readyState !== WebSocket.OPEN) return
  ws.send(JSON.stringify(obj))
}

const iAmHost = computed(() => !!myPlayerId.value && !!hostId.value && myPlayerId.value === hostId.value)

function setTurnMode(mode: 'host' | 'client' | 'random') {
  if (status.value !== 'connected') return
  if (phase.value !== 'lobby') return
  if (!iAmHost.value) return
  sendJson({ type: 'set_turn_mode', mode })
}

function onTurnModeChange(ev: Event) {
  const target = ev.target as HTMLSelectElement | null
  const value = target?.value
  if (value === 'host' || value === 'client' || value === 'random') setTurnMode(value)
}

const turnHint = computed(() => {
  if (phase.value !== 'playing') return ''
  if (winner.value) return ''
  return turn.value === myPlayerId.value ? '轮到你了' : '等待对手落子'
})

function turnModeLabel(mode: 'host' | 'client' | 'random') {
  if (mode === 'host') return '主机先手'
  if (mode === 'client') return '客户端先手'
  return '随机先手'
}

function connect() {
  if (status.value !== 'disconnected') return

  const pageHost = window.location.hostname
  const inputHost = form.serverHost.trim()
  if ((inputHost === '127.0.0.1' || inputHost === 'localhost') && pageHost && pageHost !== inputHost) {
    log(`WARNING: Host=${inputHost} is THIS device. Use your PC IP instead (e.g. ${pageHost}).`)
  }

  status.value = 'connecting'
  log(`connecting ${wsUrl.value}`)

  const ws = new WebSocket(wsUrl.value)
  wsRef.value = ws

  ws.onopen = () => {
    status.value = 'connected'
    log('ws open')
    sendJson({ type: 'hello', playerId: form.playerId, name: form.name, color: form.color, isHost: form.isHost })
  }

  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data) as WsInMessage

      if (msg.type === 'welcome') {
        myPlayerId.value = msg.playerId
        serverProtocolVersion.value = typeof msg.protocolVersion === 'number' ? msg.protocolVersion : null
        if (serverProtocolVersion.value === null) {
          log('WARNING: server protocolVersion missing; backend may be outdated. Please restart backend to latest.')
        } else if (serverProtocolVersion.value < 2) {
          log(`WARNING: server protocolVersion=${serverProtocolVersion.value} is too old; set_shape may fail. Restart backend.`)
        }
        players.value = msg.players
        phase.value = msg.phase
        boardSize.value = msg.boardSize
        turn.value = msg.turn
        hostId.value = (msg.hostId as string | null | undefined) ?? hostId.value
        turnMode.value = (msg.turnMode as any) || turnMode.value
        winner.value = msg.winner
        ready.value = new Set(msg.ready)
        colors.value = msg.colors || {}
        initBoard(msg.boardSize)
      } else if (msg.type === 'player_joined') {
        players.value = [...players.value.filter((p) => p.playerId !== msg.player.playerId), msg.player]
        if (msg.colors) colors.value = msg.colors
        if (typeof msg.hostId !== 'undefined') hostId.value = msg.hostId
        if (msg.turnMode) turnMode.value = msg.turnMode
      } else if (msg.type === 'player_left') {
        players.value = players.value.filter((p) => p.playerId !== msg.playerId)
        ready.value.delete(msg.playerId)
        phase.value = 'lobby'
        winner.value = null
        shapes.value = {}
        delete colors.value[msg.playerId]
        if (typeof msg.hostId !== 'undefined') hostId.value = msg.hostId
        if (msg.turnMode) turnMode.value = msg.turnMode
        if (typeof msg.turn !== 'undefined') turn.value = msg.turn ?? null
        initBoard(boardSize.value)
      } else if (msg.type === 'player_ready') {
        ready.value = new Set(msg.ready)
      } else if (msg.type === 'turn_mode_update') {
        hostId.value = msg.hostId
        turnMode.value = msg.turnMode
      } else if (msg.type === 'colors_update') {
        colors.value = msg.colors || {}
      } else if (msg.type === 'game_start') {
        phase.value = 'playing'
        turn.value = msg.turn
        if (typeof msg.hostId !== 'undefined') hostId.value = msg.hostId ?? hostId.value
        if (msg.turnMode) turnMode.value = msg.turnMode
        boardSize.value = msg.boardSize
        shapes.value = msg.shapes
        colors.value = msg.colors || colors.value
        winner.value = null
        initBoard(msg.boardSize)
      } else if (msg.type === 'move_applied') {
        if (!board.value[msg.y]?.[msg.x]) board.value[msg.y][msg.x] = msg.playerId
        phase.value = msg.phase
        turn.value = msg.turn
        winner.value = msg.winner

        if (msg.winner && msg.winCells && msg.winCells.length) {
          const set = new Set<string>()
          for (const p of msg.winCells) set.add(`${p.x},${p.y}`)
          winCells.value = set
        }
      }

      log(`< ${ev.data}`)
    } catch (e) {
      log(`parse error: ${String(e)}`)
    }
  }

  ws.onclose = (ev) => {
    log(`ws close code=${ev.code}`)
    status.value = 'disconnected'
    wsRef.value = null
  }

  ws.onerror = () => {
    log('ws error')
  }
}

function disconnect() {
  const ws = wsRef.value
  if (!ws) return
  ws.close()
}

const chatText = ref('')
function sendChat() {
  const text = chatText.value.trim()
  if (!text) return
  sendJson({ type: 'chat', text })
  chatText.value = ''
}

function clickCell(x: number, y: number) {
  if (status.value !== 'connected') return
  if (phase.value !== 'playing') return
  if (winner.value) return
  if (turn.value !== myPlayerId.value) return
  if (board.value[y][x]) return
  sendJson({ type: 'move', x, y })
}

// Shape picker: 5x5 grid, choose exactly 5 points, then confirm
const shapePoints = ref<Set<string>>(new Set())
const shapeCells = computed(() =>
  Array.from({ length: 25 }, (_, i) => {
    const x = i % 5
    const y = Math.floor(i / 5)
    const key = `${x},${y}`
    return { x, y, key, selected: shapePoints.value.has(key) }
  })
)

function toggleShapePoint(x: number, y: number) {
  if (status.value !== 'connected') return
  if (phase.value !== 'lobby') return
  if (iAmReady.value) return

  const key = `${x},${y}`
  const next = new Set(shapePoints.value)
  if (next.has(key)) next.delete(key)
  else {
    if (next.size >= 5) return
    next.add(key)
  }
  shapePoints.value = next
}

const canConfirmShape = computed(() => status.value === 'connected' && phase.value === 'lobby' && shapePoints.value.size === 5)
const iAmReady = computed(() => ready.value.has(myPlayerId.value))

function confirmShape() {
  if (!canConfirmShape.value) return
  if (serverProtocolVersion.value !== null && serverProtocolVersion.value < 2) {
    log(`ERROR: backend protocolVersion=${serverProtocolVersion.value} doesn't support set_shape. Restart backend.`)
    return
  }
  const points = Array.from(shapePoints.value).map((k) => {
    const [x, y] = k.split(',').map((n) => Number(n))
    return { x, y }
  })
  sendJson({ type: 'set_shape', points })
}

function resetLocalShape() {
  shapePoints.value = new Set()
}

function setColor() {
  if (status.value !== 'connected') return
  if (phase.value !== 'lobby') return
  if (iAmReady.value) return
  sendJson({ type: 'set_color', color: form.color })
}

const otherPlayer = computed(() => players.value.find((p) => p.playerId !== myPlayerId.value) || null)
const otherReady = computed(() => (otherPlayer.value ? ready.value.has(otherPlayer.value.playerId) : false))

function cellHasPoint(list: Point[] | undefined, x: number, y: number) {
  if (!list) return false
  return list.some((p) => p.x === x && p.y === y)
}

function isWinCell(x: number, y: number) {
  return winCells.value.has(`${x},${y}`)
}

onBeforeUnmount(() => {
  disconnect()
})
</script>

<template>
  <div>
    <div class="row" style="margin-bottom: 12px">
      <div class="panel" style="flex: 1; min-width: 320px">
        <div class="row">
          <label>
            Host
            <input v-model="form.serverHost" style="width: 140px" />
          </label>
          <label>
            Port
            <input v-model="form.serverPort" style="width: 80px" />
          </label>
          <label>
            Room
            <input v-model="form.roomId" style="width: 120px" />
          </label>
          <label>
            PlayerId
            <input v-model="form.playerId" style="width: 140px" />
          </label>
          <label>
            Name
            <input v-model="form.name" style="width: 120px" />
          </label>
          <label>
            Color
            <input
              type="color"
              v-model="form.color"
              style="width: 46px; height: 30px; padding: 0"
              @change="setColor"
            />
          </label>
          <label style="display: flex; align-items: center; gap: 6px">
            <input type="checkbox" v-model="form.isHost" :disabled="status !== 'disconnected'" />
            我是主机
          </label>
        </div>

        <div class="row" style="margin-top: 10px">
          <button @click="connect" :disabled="status !== 'disconnected'">Connect</button>
          <button @click="disconnect" :disabled="status === 'disconnected'">Disconnect</button>
          <button @click="resetLocalShape" :disabled="phase !== 'lobby' || iAmReady">Reset Shape</button>
          <span>Status: {{ status }} | Phase: {{ phase }}</span>
          <span style="opacity: 0.8">{{ wsUrl }}</span>
        </div>

        <div class="row" style="margin-top: 10px">
          <span>Me: {{ form.name }} ({{ myPlayerId || form.playerId }})</span>
          <span v-if="otherPlayer">Opponent: {{ otherPlayer.name }} ({{ otherPlayer.playerId }})</span>
          <span v-else style="opacity: 0.7">Waiting opponent…</span>
        </div>

        <div class="row" style="margin-top: 10px" v-if="phase === 'lobby'">
          <span>Ready: {{ iAmReady ? 'yes' : 'no' }} / Opponent: {{ otherReady ? 'yes' : 'no' }}</span>
          <label style="margin-left: 10px">
            先手
            <select
              :value="turnMode"
              :disabled="!iAmHost || status !== 'connected' || phase !== 'lobby'"
              @change="onTurnModeChange"
            >
              <option value="host">{{ turnModeLabel('host') }}</option>
              <option value="client">{{ turnModeLabel('client') }}</option>
              <option value="random">{{ turnModeLabel('random') }}</option>
            </select>
          </label>
          <button @click="confirmShape" :disabled="!canConfirmShape || iAmReady">Confirm Shape</button>
          <span style="opacity: 0.7">(select exactly 5 cells)</span>
        </div>

        <div class="row" style="margin-top: 10px">
          <input
            v-model="chatText"
            placeholder="chat"
            style="flex: 1; min-width: 200px"
            @keydown.enter="sendChat"
          />
          <button @click="sendChat" :disabled="status !== 'connected'">Send</button>
        </div>
      </div>

      <div class="panel" style="flex: 1; min-width: 320px">
        <div class="log">{{ logLines.join('\n') }}</div>
      </div>
    </div>

    <div class="row" style="margin-bottom: 12px">
      <div class="panel" style="flex: 1; min-width: 320px">
        <div style="margin-bottom: 8px">Your win shape (5×5)</div>
        <div class="grid" style="grid-template-columns: repeat(5, 28px)" v-if="phase === 'lobby'">
          <button
            v-for="c in shapeCells"
            :key="c.key"
            class="cell"
            :disabled="status !== 'connected' || phase !== 'lobby' || iAmReady"
            @click="toggleShapePoint(c.x, c.y)"
            :title="c.key"
            :style="c.selected ? 'background:#dff1ff;border-color:#77b7e5' : ''"
          >
            {{ c.selected ? 'X' : '' }}
          </button>
        </div>

        <div v-else class="grid" style="grid-template-columns: repeat(5, 28px)">
          <button v-for="i in 25" :key="i" class="cell" disabled>
            {{ cellHasPoint(shapes[myPlayerId], (i - 1) % 5, Math.floor((i - 1) / 5)) ? 'X' : '' }}
          </button>
        </div>
      </div>

      <div class="panel" style="flex: 1; min-width: 320px">
        <div style="margin-bottom: 8px">Opponent win shape (5×5)</div>
        <div class="grid" style="grid-template-columns: repeat(5, 28px)">
          <button v-for="i in 25" :key="i" class="cell" disabled>
            {{
              otherPlayer
                ? cellHasPoint(shapes[otherPlayer.playerId], (i - 1) % 5, Math.floor((i - 1) / 5))
                  ? 'X'
                  : ''
                : ''
            }}
          </button>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="row" style="margin-bottom: 10px">
        <span>Turn: {{ turn || '-' }}</span>
        <span v-if="turnHint">{{ turnHint }}</span>
        <span v-if="winner">Winner: {{ winner }}</span>
        <span v-else style="opacity: 0.8">(server validates moves & win)</span>
      </div>

      <div class="grid" :style="`grid-template-columns: repeat(${boardSize}, 28px)`">
        <button
          v-for="c in cells"
          :key="`${c.x}-${c.y}`"
          class="cell"
          :disabled="status !== 'connected' || phase !== 'playing' || !!c.cell || !!winner || turn !== myPlayerId"
          @click="clickCell(c.x, c.y)"
          :title="`${c.x},${c.y}`"
          :style="isWinCell(c.x, c.y) && winner ? `background:${colorFor(winner)}` : ''"
        >
          <span v-if="c.cell" class="stone" :style="`background:${colorFor(c.cell)}`" />
        </button>
      </div>
    </div>
  </div>
</template>
