/**
 * TailwindCSS v4 reads its design tokens from CSS (`@theme` in src/index.css).
 * This file remains for tooling that expects a config entrypoint and to pin the
 * content globs used by editor extensions and IDE IntelliSense.
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        aurora: {
          50: '#eef4ff',
          100: '#d9e5ff',
          200: '#bcd0ff',
          300: '#8eb0ff',
          400: '#5985ff',
          500: '#3560ff',
          600: '#1f3ef5',
          700: '#182ddc',
          800: '#1a28b1',
          900: '#1c298b',
        },
      },
      backdropBlur: {
        glass: '28px',
      },
      boxShadow: {
        glass: '0 8px 32px rgba(8, 12, 32, 0.37)',
        'glass-inset': 'inset 0 1px 0 rgba(255, 255, 255, 0.12)',
      },
    },
  },
  plugins: [],
};
