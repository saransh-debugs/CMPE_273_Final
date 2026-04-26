/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Parchment-on-charcoal palette
        ink: {
          900: "#0F0E0C",   // base background — warm near-black
          800: "#15130F",
          700: "#1A1714",   // surface (cards, panels)
          600: "#221E19",   // surface elevated
          500: "#2A2521",   // hairline / divider
        },
        cream: {
          50: "#F5F1E8",    // primary text — warm parchment
          100: "#E8E2D4",
          300: "#A8A299",   // secondary
          500: "#6B6760",   // muted
          700: "#3A3833",   // deep muted
        },
        // The accent — single cherry red, used for emphasis + high-blame
        cherry: {
          DEFAULT: "#C73E1D",
          light: "#E55A37",
          deep: "#8E2A12",
        },
        amber: "#D9933A",   // warning / medium blame
        sage: "#7A8B5E",    // healthy / low blame
        slate: "#6B7B8C",   // neutral info
      },
      fontFamily: {
        // Editorial display — Fraunces with optical sizing, used italicized
        display: ['"Fraunces"', "Georgia", "serif"],
        // UI chrome
        sans: ['"Manrope"', "system-ui", "sans-serif"],
        // Data / IDs / code — JetBrains Mono is the right fit semantically
        mono: ['"JetBrains Mono"', '"Menlo"', "monospace"],
      },
      letterSpacing: {
        tightest: "-0.04em",
        tighter: "-0.02em",
      },
      animation: {
        "fade-in": "fadeIn 0.4s ease-out forwards",
        "rise": "rise 0.5s cubic-bezier(0.16, 1, 0.3, 1) forwards",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        rise: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};
