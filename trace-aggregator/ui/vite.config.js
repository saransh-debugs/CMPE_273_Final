/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
    plugins: [react()],
    test: {
        environment: "jsdom",
        globals: true,
        setupFiles: ["./vitest.setup.ts"],
    },
    server: {
        port: 5173,
        proxy: {
            // Proxy API calls to FastAPI so the team doesn't need CORS to run locally.
            "/api": {
                target: "http://localhost:8000",
                changeOrigin: true,
                rewrite: function (path) { return path.replace(/^\/api/, ""); },
            },
        },
    },
});
