import { useEffect, useRef, useState } from 'react'

export function CommandBar({ onSubmit }: { onSubmit: (verb: string, arg: string) => void }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === ':' && !open) {
        e.preventDefault()
        setOpen(true)
        setTimeout(() => inputRef.current?.focus(), 0)
      } else if (e.key === 'Escape') {
        setOpen(false)
        setText('')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  function submit() {
    const parts = text.trim().split(/\s+/)
    const verb = (parts[0] || '').toLowerCase()
    let arg = parts[1] || ''
    if (arg && !arg.includes(':')) arg = arg.toUpperCase()
    if (verb) onSubmit(verb, arg)
    setOpen(false)
    setText('')
  }

  if (!open) return null
  return (
    <div className="border-t border-neutral-800 px-2 py-1 bg-neutral-950">
      <span className="text-amber-400">: </span>
      <input
        ref={inputRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') submit()
        }}
        className="bg-transparent outline-none text-sm w-3/4"
        placeholder="chart AAPL · depth · tf 15m · source live"
      />
    </div>
  )
}
