import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: parseInt(process.env.PORT || '5173', 10),
    proxy: {
      '/api': process.env.API_TARGET || 'http://localhost:8000',
      '/health': process.env.API_TARGET || 'http://localhost:8000',
    },
  },
});
