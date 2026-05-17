/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        actNow:     "#ef4444",
        checkToday: "#f59e0b",
        watch:      "#eab308",
        fyi:        "#6b7280",
      },
    },
  },
  plugins: [],
};
