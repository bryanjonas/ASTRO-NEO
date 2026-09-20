import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/app/',
  build: {
    outDir: 'dist',
  },
  server: {
    proxy: {
      '/api': { target: 'http://localhost:18080', changeOrigin: true },
      '/ws': { target: 'ws://localhost:18080', ws: true },
    },
  },
})
