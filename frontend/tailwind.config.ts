import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#121212",
        field: "#f7f2e8",
        line: "#d9cfbf",
        cobalt: "#1b2a14",
        moss: "#5a7d00",
        amber: "#916400",
        rosewood: "#9b364a"
      },
      boxShadow: {
        panel: "0 10px 28px rgba(18, 18, 18, 0.08)",
        float: "0 20px 32px rgba(18, 18, 18, 0.12)"
      },
      borderRadius: {
        xl2: "18px"
      }
    }
  },
  plugins: []
} satisfies Config;
