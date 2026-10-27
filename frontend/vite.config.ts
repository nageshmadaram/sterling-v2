import { execFileSync } from 'node:child_process';
import { defineConfig } from 'vite';
import { resolveBuildSha } from './buildIdentity';
import react from '@vitejs/plugin-react';
import path from 'node:path';

function retirePlayerDocuments(req: { url?: string }, res: { writeHead: (status: number, headers: Record<string,string>) => unknown; end: (body?: string) => unknown }, next: () => void) {
  let pathname: string;
  try { pathname = path.posix.normalize(decodeURIComponent(new URL(req.url || '/', 'http://localhost').pathname)); }
  catch { res.writeHead(400, { 'Cache-Control': 'no-store' }); res.end(); return; }
  if (/(?:^|\/)(?:(?:tv|radio)\/.*\.html|media_templates\/(?:tv|radio)\.html)$/i.test(pathname)) {
    res.writeHead(410, { 'Cache-Control': 'no-store', 'Content-Type': 'text/plain' });
    res.end('Open this player from the Sterling launcher.'); return;
  }
  next();
}

export default defineConfig({
  plugins: [react(), {
    name: 'private-player-document-boundary',
    configureServer(server) { server.middlewares.use(retirePlayerDocuments); },
    configurePreviewServer(server) { server.middlewares.use(retirePlayerDocuments); },
  }],
  // Every browser request uses the same origin; API_TARGET selects the local upstream.
  define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify(''),
    'import.meta.env.VITE_BUILD_SHA': JSON.stringify(resolveBuildSha(() => {
      const root = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim();
      // Archives inside another checkout must use their publisher's immutable SHA.
      if (path.resolve(root, 'frontend') !== path.resolve(import.meta.dirname)) throw new Error('Archived frontend build');
      return execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' });
    }, process.env.STERLING_BUILD_SHA)),
    'import.meta.env.VITE_API_CONTRACT': JSON.stringify('sterling-api-v1') },
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
    },
  },
  // Preserve hashed assets still referenced by open tabs during local rebuilds.
  build: {
    emptyOutDir: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) return 'vendor-react';
          if (id.includes('/node_modules/@tanstack/')) return 'vendor-query';
          if (id.includes('/node_modules/lightweight-charts/')) return 'vendor-charts';
        },
      },
    },
  },
  server: {
    fs: { deny: ['.env', '.env.*', '*.{crt,pem}', '**/.git/**', '**/media_templates/**'] },
    allowedHosts: true,
    port: parseInt(process.env.PORT || '5173', 10),
    proxy: {
      '/api': { target: process.env.API_TARGET || 'http://localhost:8000', ws: true },
      '/tma': { target: process.env.API_TARGET || 'http://localhost:8000' },
      '/health': process.env.API_TARGET || 'http://localhost:8000',
    },
  },
});
