/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        paper: '#ffffff',
        sand: '#f7f5f2',
        ink: '#000000',
        mute: '#6b6b6b',
        faint: '#9a9a9a',
        hair: '#e6e4e0',
        critical: '#c1362c',
        brass: '#8a6a24',
        wall: '#ffffff',
        card: '#ffffff',
      },
      fontFamily: {
        display: ['Instrument Serif', 'ui-serif', 'Georgia', 'serif'],
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        paper: '0 1px 2px rgba(0,0,0,.04)',
      },
      borderRadius: { DEFAULT: '4px' },
    },
  },
  plugins: [],
}

