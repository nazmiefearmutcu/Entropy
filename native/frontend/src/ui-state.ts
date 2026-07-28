/** Overlay + layout state, kept out of the 10 Hz snapshot channel. */
import { useSyncExternalStore } from 'react'

export type PickerMode = 'focus' | 'watch'
export type SettingsTab = 'general' | 'chart' | 'scanner' | 'feeds' | 'bot'

export interface UiState {
  picker: PickerMode | null
  settings: SettingsTab | null
  command: boolean
  botOpen: boolean
  botHeight: number
  railLeft: number
  railRight: number
}

const STORAGE_KEY = 'entropy.layout.v1'

function loadLayout(): Pick<UiState, 'botOpen' | 'botHeight' | 'railLeft' | 'railRight'> {
  const fallback = { botOpen: true, botHeight: 268, railLeft: 252, railRight: 304 }
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY)
    if (!raw) return fallback
    const parsed = JSON.parse(raw) as Partial<UiState>
    return {
      botOpen: parsed.botOpen ?? fallback.botOpen,
      botHeight: clamp(parsed.botHeight ?? fallback.botHeight, 160, 620),
      railLeft: clamp(parsed.railLeft ?? fallback.railLeft, 190, 420),
      railRight: clamp(parsed.railRight ?? fallback.railRight, 220, 460),
    }
  } catch {
    return fallback
  }
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v))
}

let state: UiState = {
  picker: null,
  settings: null,
  command: false,
  ...loadLayout(),
}

const subs = new Set<() => void>()

function emit() {
  for (const f of subs) f()
}

function persist() {
  try {
    globalThis.localStorage?.setItem(
      STORAGE_KEY,
      JSON.stringify({
        botOpen: state.botOpen,
        botHeight: state.botHeight,
        railLeft: state.railLeft,
        railRight: state.railRight,
      }),
    )
  } catch {
    /* private mode / no storage: layout simply is not remembered */
  }
}

function set(patch: Partial<UiState>, save = false) {
  state = { ...state, ...patch }
  if (save) persist()
  emit()
}

export function openPicker(mode: PickerMode = 'focus') {
  set({ picker: mode, settings: null })
}
export function closePicker() {
  set({ picker: null })
}
export function openSettings(tab: SettingsTab = 'general') {
  set({ settings: tab, picker: null })
}
export function closeSettings() {
  set({ settings: null })
}
export function setSettingsTab(tab: SettingsTab) {
  set({ settings: tab })
}
export function setCommandOpen(open: boolean) {
  set({ command: open })
}
export function toggleBot() {
  set({ botOpen: !state.botOpen }, true)
}
export function setBotOpen(open: boolean) {
  set({ botOpen: open }, true)
}
export function resizeBot(delta: number) {
  set({ botHeight: clamp(state.botHeight - delta, 160, 620) }, true)
}
export function resizeRailLeft(delta: number) {
  set({ railLeft: clamp(state.railLeft + delta, 190, 420) }, true)
}
export function resizeRailRight(delta: number) {
  set({ railRight: clamp(state.railRight - delta, 220, 460) }, true)
}

export function getUi(): UiState {
  return state
}

function subscribe(cb: () => void) {
  subs.add(cb)
  return () => {
    subs.delete(cb)
  }
}

export function useUi(): UiState {
  return useSyncExternalStore(
    subscribe,
    () => state,
    () => state,
  )
}
