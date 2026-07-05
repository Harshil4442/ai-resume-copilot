import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx}",
    "./components/**/*.{js,ts,jsx,tsx}",
    "./lib/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#020617",
        surface: "#0f172a",
        primary: "#3b82f6",
      },
      transitionTimingFunction: {
        'spring': 'cubic-bezier(0.32, 0.72, 0, 1)',
      },
      keyframes: {
        'fade-in': {
          '0%': { opacity: '0', transform: 'translateY(1rem)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'shimmer': {
          '0%': { backgroundPosition: '0 0' },
          '100%': { backgroundPosition: '-200% 0' },
        },
        'marquee': {
          '0%': { transform: 'translateX(0)' },
          '100%': { transform: 'translateX(calc(-100% - var(--gap)))' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.7s cubic-bezier(0.32, 0.72, 0, 1) forwards',
        'shimmer': 'shimmer 2s linear infinite',
        'marquee': 'marquee var(--duration) linear infinite',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
};

export default config;
