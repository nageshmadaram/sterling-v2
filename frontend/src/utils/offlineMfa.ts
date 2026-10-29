/** In-memory MFA step-up only; never persistent local/session storage. */
let proof: string | null = null;
export function currentOfflineMfaProof(): string | null { return proof; }
export function setOfflineMfaProof(value: string | null): void {
  proof = value && value.length <= 4096 ? value : null;
}
export function clearOfflineMfaProof(): void { proof = null; }
