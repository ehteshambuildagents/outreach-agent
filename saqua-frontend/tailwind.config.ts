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
        success: "var(--success)",
        "success-soft": "var(--success-soft)",
        warn: "var(--warn)",
        "warn-soft": "var(--warn-soft)",
        danger: "var(--danger)",
        "danger-soft": "var(--danger-soft)",
        amber: "var(--amber)",
        "amber-soft": "var(--amber-soft)",
        peach: "var(--peach)",
        "indigo-soft": "var(--indigo-soft)",
      },
      backgroundImage: {
        "grad-brand": "var(--grad-brand)",
        "grad-brand-soft": "var(--grad-brand-soft)",
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
        "6xl": ["64px", { lineHeight: "1.03" }],
        "7xl": ["84px", { lineHeight: "1.0" }],
      },
      fontFamily: {
        sans: ["var(--font-sans)", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
        // Display — General Sans, for marketing headlines and big type.
        display: ["var(--font-display)", "Inter", "system-ui", "sans-serif"],
        // Reserved for exactly one thing: Saqua's own generated email copy (upright serif).
        serif: ["var(--font-serif)", "Georgia", "Cambria", "serif"],
        // Data-like elements: fit scores, timestamps, status tags, technical labels.
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        pop: "0 24px 70px rgba(17,17,17,.16)",
        card: "0 6px 24px rgba(17,17,17,.06), 0 1px 3px rgba(17,17,17,.04)",
        nav: "0 10px 34px rgba(17,17,17,.09), 0 2px 8px rgba(17,17,17,.05)",
        glow: "0 0 0 1px var(--accent-line), 0 14px 46px -14px var(--accent-soft)",
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
