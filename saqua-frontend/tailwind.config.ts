import type { Config } from "tailwindcss";

/**
 * Saqua design system — mapped 1:1 from the reference (dark canvas, single purple
 * accent) reusing the product's proven spacing / radius / type / motion scales.
 * Colors are driven by CSS variables (see globals.css) so theming stays central.
 */
const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        panel: "var(--panel)",
        card: "var(--card)",
        "card-2": "var(--card-2)",
        raised: "var(--raised)",
        border: "var(--border)",
        "border-faint": "var(--border-faint)",
        "border-strong": "var(--border-strong)",
        hover: "var(--hover)",
        "hover-strong": "var(--hover-strong)",
        text: "var(--text)",
        "text-2": "var(--text-2)",
        muted: "var(--muted)",
        faint: "var(--faint)",
        accent: {
          DEFAULT: "var(--accent)",
          hi: "var(--accent-hi)",
          soft: "var(--accent-soft)",
          line: "var(--accent-line)",
          ink: "var(--accent-ink)",
        },
        live: "var(--live)",
        "live-soft": "var(--live-soft)",
        success: "var(--success)",
        "success-soft": "var(--success-soft)",
        warn: "var(--warn)",
        "warn-soft": "var(--warn-soft)",
        danger: "var(--danger)",
        "danger-soft": "var(--danger-soft)",
      },
      borderRadius: {
        xs: "6px",
        sm: "8px",
        md: "10px",
        lg: "12px",
        xl: "16px",
        "2xl": "20px",
      },
      fontSize: {
        "2xs": ["12px", { lineHeight: "1.4" }],
        xs: ["13px", { lineHeight: "1.5" }],
        sm: ["14px", { lineHeight: "1.55" }],
        base: ["15px", { lineHeight: "1.6" }],
        lg: ["18px", { lineHeight: "1.4" }],
        xl: ["21px", { lineHeight: "1.3" }],
        "2xl": ["26px", { lineHeight: "1.2" }],
        "3xl": ["33px", { lineHeight: "1.15" }],
        "4xl": ["42px", { lineHeight: "1.08" }],
        "5xl": ["52px", { lineHeight: "1.05" }],
      },
      fontFamily: {
        sans: ["var(--font-sans)", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
        // Reserved for exactly one thing: Saqua's own generated email copy (upright serif).
        serif: ["var(--font-serif)", "Georgia", "Cambria", "serif"],
        // Data-like elements: fit scores, timestamps, status tags, technical labels.
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        pop: "0 20px 48px -16px rgba(15,23,42,.24), 0 2px 8px -2px rgba(15,23,42,.08)",
        card: "0 1px 2px rgba(15,23,42,.04), 0 1px 3px -1px rgba(15,23,42,.05)",
        "card-lg": "0 4px 12px -4px rgba(15,23,42,.08), 0 2px 6px -2px rgba(15,23,42,.05)",
        glow: "0 0 0 1px var(--accent-line), 0 8px 32px -12px rgba(31,122,212,.28)",
      },
      transitionTimingFunction: {
        smooth: "cubic-bezier(.22,.61,.36,1)",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
        "pulse-soft": {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
        // Water/wave feel: the mark recedes out to the right, then washes back
        // in from the left. Plays once on hover (see Logo).
        "wave-wipe": {
          "0%": { clipPath: "inset(0 0 0 0)" },
          "42%": { clipPath: "inset(0 0 0 100%)" },
          "50%": { clipPath: "inset(0 100% 0 0)" },
          "100%": { clipPath: "inset(0 0 0 0)" },
        },
        // Ambient bloom drift — very slow float + breathe so the depth behind the
        // glass never sits perfectly still. Two variants push opposite directions
        // for an organic parallax. Disabled under prefers-reduced-motion (globals).
        drift: {
          "0%,100%": { transform: "translate(0,0) scale(1)" },
          "50%": { transform: "translate(4%,-3%) scale(1.05)" },
        },
        "drift-2": {
          "0%,100%": { transform: "translate(0,0) scale(1)" },
          "50%": { transform: "translate(-5%,4%) scale(1.08)" },
        },
      },
      animation: {
        "fade-up": "fade-up .35s cubic-bezier(.22,.61,.36,1) both",
        "pulse-soft": "pulse-soft 1.6s ease-in-out infinite",
        "wave-wipe": "wave-wipe .9s ease-in-out",
        drift: "drift 28s ease-in-out infinite",
        "drift-2": "drift-2 34s ease-in-out infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
