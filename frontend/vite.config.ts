import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  base: "/v2/",
  server: { proxy: { "/v1": "http://127.0.0.1:8765" } },
  test: { environment: "jsdom" },
});
