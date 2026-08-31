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
        wall: '#faf8f3',
        card: '#ffffff',
        ink: '#1d1a16',
        mute: '#6f675b',
        faint: '#7d7466',
        hair: '#e7e1d5',
        brass: '#8a6a24',
        critical: '#a33a26',
      },
      fontFamily: {
        serif: ['Newsreader', 'Georgia', 'serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        paper: '0 1px 2px rgba(29,26,22,.05), 0 8px 28px rgba(29,26,22,.07)',
      },
    },
  },
  plugins: [],
}

