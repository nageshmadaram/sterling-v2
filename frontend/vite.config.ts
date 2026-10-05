import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
    },
  },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: 'react', test: /node_modules[\/](react|react-dom|scheduler)[\/]/, priority: 30 },
            { name: 'charts', test: /node_modules[\/]lightweight-charts[\/]/, priority: 20 },
            { name: 'vendor', test: /node_modules[\/]/, priority: 10, maxSize: 350_000 },
            { name: 'app', test: /[\/]src[\/]/, priority: 1, maxSize: 350_000, entriesAware: true },
          ],
        },
      },
    },
  },
  server: {
    port: parseInt(process.env.PORT || '5173', 10),
    proxy: {
      '/api': { target: process.env.API_TARGET || 'http://localhost:8000', ws: true },
      '/health': process.env.API_TARGET || 'http://localhost:8000',
    },
  },
});
