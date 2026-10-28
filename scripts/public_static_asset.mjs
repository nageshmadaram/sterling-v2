// This policy is independent of Sterling's broker and HTTP proxy.
import fs from 'node:fs';
import path from 'node:path';

/** Resolve only public files inside the active immutable frontend build.
 * Invalid or hidden paths, symlink escapes and missing extensionful assets
 * are denied. Ordinary client routes still resolve to index.html.
 */
export function publicStaticFile(root, pathname) {
  let decoded;
  try { decoded = decodeURIComponent(pathname); }
  catch { return null; }
  if (!decoded.startsWith('/') || decoded.includes('\0') || decoded.includes('\\') ||
      decoded.split('/').some(part => part.startsWith('.'))) return null;
  const base = path.resolve(root);
  const candidate = path.resolve(base, '.' + decoded);
  if (candidate !== base && !candidate.startsWith(base + path.sep)) return null;

  let canonicalRoot;
  try { canonicalRoot = fs.realpathSync(base); }
  catch { return null; }
  const contained = filename => filename === canonicalRoot ||
    filename.startsWith(canonicalRoot + path.sep);
  const safeFile = filename => {
    try {
      if (!fs.statSync(filename).isFile()) return false;
      return contained(fs.realpathSync(filename));
    } catch { return false; }
  };
  if (safeFile(candidate)) return candidate;
  // For extensionless SPA routes, reject every existing out-of-tree target or
  // parent symlink before considering index.html. This includes directory
  // symlinks and missing names inside an escaped directory.
  let probe = candidate;
  while (true) {
    try {
      if (!contained(fs.realpathSync(probe))) return null;
      break;
    } catch (error) {
      if (error.code !== 'ENOENT' && error.code !== 'ENOTDIR') return null;
      if (probe === base) return null;
      probe = path.dirname(probe);
    }
  }
  if (path.extname(decoded) || /^\/(docs|redoc|openapi)(\/|$)/.test(decoded)) return null;
  const index = path.join(base, 'index.html');
  return safeFile(index) ? index : null;
}
