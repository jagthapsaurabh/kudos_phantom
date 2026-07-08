/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'phantom-dark': '#0f172a',
        'phantom-blue': '#3b82f6',
        'phantom-green': '#10b981',
        'phantom-red': '#ef4444',
      }
    },
  },
  plugins: [],
}
