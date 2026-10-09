/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: { extend: {
    colors: {
      background: '#0a111c', panel: '#101b2a', surface: '#152338', line: '#24334a',
      muted: '#8ca0bb', accent: '#5ee5b2', blue: '#71b0ff',
    },
    fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'], mono: ['JetBrains Mono', 'ui-monospace', 'monospace'] },
  } },
  plugins: [],
}
