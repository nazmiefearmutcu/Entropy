import { useSyncExternalStore } from 'react'
import type { SnapshotMessage } from './contract'

let snap: SnapshotMessage | null = null
let connected = false
const subs = new Set<() => void>()

function emit() {
  subs.forEach((f) => f())
}

export function setSnap(m: SnapshotMessage) {
  snap = m
  emit()
}

export function setConnected(c: boolean) {
  if (c !== connected) {
    connected = c
    emit()
  }
}

function subscribe(cb: () => void) {
  subs.add(cb)
  return () => subs.delete(cb)
}

export function useSnap() {
  return useSyncExternalStore(subscribe, () => snap)
}

export function useConnected() {
  return useSyncExternalStore(subscribe, () => connected)
}
