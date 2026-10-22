/** Prefer checkout provenance; Git-less images use their explicit build input. */
export function resolveBuildSha(readGit: () => string, artifactSha: unknown): string {
  const valid = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{40}$/i.test(value.trim()) ? value.trim().toLowerCase() : 'UNKNOWN';
  try { return valid(readGit()); }
  catch { return valid(artifactSha); }
}
