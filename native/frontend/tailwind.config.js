/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: 'var(--s-base)',
        sunken: 'var(--s-sunken)',
        panel: 'var(--s-panel)',
        raised: 'var(--s-raised)',
        hover: 'var(--s-hover)',
        activefill: 'var(--s-active)',
        line: {
          DEFAULT: 'var(--line)',
          strong: 'var(--line-strong)',
        },
        ink: {
          DEFAULT: 'var(--ink)',
          dim: 'var(--ink-dim)',
          mute: 'var(--ink-mute)',
          faint: 'var(--ink-faint)',
        },
        up: 'var(--up)',
        down: 'var(--down)',
        accent: 'var(--accent)',
        info: 'var(--info)',
        warn: 'var(--warn)',
        'up-soft': 'var(--up-soft)',
        'down-soft': 'var(--down-soft)',
        'accent-soft': 'var(--accent-soft)',
        'info-soft': 'var(--info-soft)',
      },
      fontFamily: {
        ui: 'var(--font-ui)',
        mono: 'var(--font-mono)',
      },
      fontSize: {
        micro: ['10px', '13px'],
        xs: ['11px', '14px'],
        sm: ['12px', '16px'],
        base: ['13px', '18px'],
        md: ['15px', '20px'],
        lg: ['18px', '22px'],
        xl: ['22px', '26px'],
        hero: ['30px', '32px'],
      },
      letterSpacing: {
        label: '0.14em',
        mark: '0.28em',
      },
      boxShadow: {
        drawer: '-18px 0 40px -12px rgba(0, 0, 0, 0.75)',
        pop: '0 18px 44px -14px rgba(0, 0, 0, 0.85)',
      },
    },
  },
  plugins: [],
}
