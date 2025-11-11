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
        'churn-primary': '#2563eb',
        'churn-secondary': '#f59e0b',
        'churn-success': '#10b981',
        'churn-danger': '#ef4444',
      },
    },
  },
  plugins: [],
}

