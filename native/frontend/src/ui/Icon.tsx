/** Hand-drawn 16px stroke icon set. No icon font, no emoji, no external assets. */

export type IconName =
  | 'search'
  | 'gear'
  | 'star'
  | 'star-filled'
  | 'close'
  | 'plus'
  | 'chevron-down'
  | 'chevron-up'
  | 'chevron-right'
  | 'play'
  | 'stop'
  | 'pause'
  | 'resume'
  | 'halt'
  | 'link'
  | 'link-off'
  | 'mark'

const PATHS: Record<IconName, string> = {
  search: 'M7 1.6a5.4 5.4 0 1 0 3.4 9.6l3.2 3.2 1.1-1.1-3.2-3.2A5.4 5.4 0 0 0 7 1.6Zm0 1.5a3.9 3.9 0 1 1 0 7.8 3.9 3.9 0 0 1 0-7.8Z',
  gear: 'M8 5.4a2.6 2.6 0 1 0 0 5.2 2.6 2.6 0 0 0 0-5.2Zm0 1.5a1.1 1.1 0 1 1 0 2.2 1.1 1.1 0 0 1 0-2.2Zm-1-6h2l.3 1.8 1.3.6 1.6-.9 1.4 1.4-.9 1.6.6 1.3 1.8.3v2l-1.8.3-.6 1.3.9 1.6-1.4 1.4-1.6-.9-1.3.6-.3 1.8H7l-.3-1.8-1.3-.6-1.6.9-1.4-1.4.9-1.6-.6-1.3L.9 9V7l1.8-.3.6-1.3-.9-1.6 1.4-1.4 1.6.9 1.3-.6L7 .9Z',
  star: 'M8 1.8 9.9 5.7l4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6ZM8 4.6 6.9 6.9l-2.5.4 1.8 1.7-.4 2.5L8 10.3l2.2 1.2-.4-2.5 1.8-1.7-2.5-.4Z',
  'star-filled': 'M8 1.8 9.9 5.7l4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6Z',
  close: 'M3.4 2.3 8 6.9l4.6-4.6 1.1 1.1L9.1 8l4.6 4.6-1.1 1.1L8 9.1l-4.6 4.6-1.1-1.1L6.9 8 2.3 3.4Z',
  plus: 'M7.25 2.5h1.5v4.75H13.5v1.5H8.75V13.5h-1.5V8.75H2.5v-1.5h4.75Z',
  'chevron-down': 'M3.6 5.9 8 10.3l4.4-4.4-1.1-1.1L8 8.1 4.7 4.8Z',
  'chevron-up': 'M12.4 10.1 8 5.7l-4.4 4.4 1.1 1.1L8 7.9l3.3 3.3Z',
  'chevron-right': 'M5.9 12.4 10.3 8 5.9 3.6 4.8 4.7 8.1 8l-3.3 3.3Z',
  play: 'M4.5 2.6 13 8l-8.5 5.4Z',
  stop: 'M3.6 3.6h8.8v8.8H3.6Z',
  pause: 'M4 3h3v10H4Zm5 0h3v10H9Z',
  resume: 'M4 3h2v10H4Zm4 0 7 5-7 5Z',
  halt: 'M5.2 1.7h5.6L14.3 5.2v5.6l-3.5 3.5H5.2L1.7 10.8V5.2ZM7.25 4.2v5h1.5v-5Zm0 6.2v1.6h1.5v-1.6Z',
  link: 'M6.6 9.4a3 3 0 0 1 0-4.2l2-2a3 3 0 0 1 4.2 4.2l-1 1-1.1-1.1 1-1a1.4 1.4 0 0 0-2-2l-2 2a1.4 1.4 0 0 0 0 2Zm2.8-2.8a3 3 0 0 1 0 4.2l-2 2a3 3 0 0 1-4.2-4.2l1-1 1.1 1.1-1 1a1.4 1.4 0 0 0 2 2l2-2a1.4 1.4 0 0 0 0-2Z',
  'link-off':
    'M2.2 1.1 14.9 13.8l-1.1 1.1L1.1 2.2ZM6.6 9.4a3 3 0 0 1 0-4.2l.7-.7 1.1 1.1-.7.7a1.4 1.4 0 0 0 0 2Zm4.5-4.5 1-1 1.1 1.1-1 1Z',
  mark: 'M1.6 12.6h2.6V4.2H1.6Zm4.6 1.8h2.6V1.6H6.2Zm4.6-3.2h2.6V6.4h-2.6Z',
}

export function Icon({
  name,
  size = 14,
  className = '',
}: {
  name: IconName
  size?: number
  className?: string
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
      className={`shrink-0 ${className}`}
      fill="currentColor"
    >
      <path d={PATHS[name]} fillRule="evenodd" clipRule="evenodd" />
    </svg>
  )
}
