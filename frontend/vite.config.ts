import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  // Every browser request uses the same origin; API_TARGET selects the local upstream.
  define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('') },
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
    },
  },
  // Preserve hashed assets still referenced by open tabs during local rebuilds.
  build: { emptyOutDir: false },
  // Default splitting preserves module initialization order across shared live-tick state.
  server: {
    allowedHosts: true,
    port: parseInt(process.env.PORT || '5173', 10),
    proxy: {
      '/api': { target: process.env.API_TARGET || 'http://localhost:8000', ws: true },
      '/tma': { target: process.env.API_TARGET || 'http://localhost:8000' },
      '/health': process.env.API_TARGET || 'http://localhost:8000',
    },
  },
});
