/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Severity tokens — intentional attention colors, do NOT change
        actNow:     "#ef4444",
        checkToday: "#f59e0b",
        watch:      "#eab308",
        fyi:        "#6b7280",

        // Quiet Paper palette re-theme
        // blue → persimmon family (buttons, links, accents)
        blue: {
          50:  '#F5E8DB',
          100: '#F1DDC9',
          200: '#E9C6A6',
          300: '#E2A57E',
          400: '#DD7E55',
          500: '#D85F3B',
          600: '#D85F3B',
          700: '#B8502F',
          800: '#974226',
          900: '#7A371F',
          950: '#5C2917',
        },

        // slate → warm neutrals (backgrounds, text, borders)
        slate: {
          50:  '#FAF8F3',
          100: '#F5F0E6',
          200: '#E8DFD0',
          300: '#D9CDB8',
          400: '#A89B89',
          500: '#8B8070',
          600: '#7A7268',
          700: '#544B3D',
          800: '#433C30',
          900: '#3A342B',
          950: '#2A2620',
        },

        // white → warm card surface
        white: '#FFFCF6',
      },
    },
  },
  plugins: [],
};
