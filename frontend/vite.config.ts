import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// В разработке /api проксируется в backend (uvicorn :8000); в Docker это делает nginx.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
    fs: { strict: false }, // node_modules может лежать вне папки проекта (junction)
  },
  build: {
    chunkSizeWarningLimit: 1200, // ECharts тяжёлый; он грузится отдельным чанком (dynamic import графика)
  },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
