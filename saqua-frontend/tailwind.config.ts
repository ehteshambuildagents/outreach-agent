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
        sans: ["var(--font-inter)", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
      },
      boxShadow: {
        pop: "0 16px 50px rgba(0,0,0,.55)",
        card: "0 1px 0 rgba(255,255,255,.02) inset, 0 1px 2px rgba(0,0,0,.4)",
        glow: "0 0 0 1px var(--accent-line), 0 8px 40px -12px var(--accent-soft)",
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
      },
      animation: {
        "fade-up": "fade-up .35s cubic-bezier(.22,.61,.36,1) both",
        "pulse-soft": "pulse-soft 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
