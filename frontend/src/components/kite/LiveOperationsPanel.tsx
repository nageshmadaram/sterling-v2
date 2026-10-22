/**
 * Operations.
 *
 * One screen an operator can act on without a terminal: whether trading is
 * enabled, what the broker actually holds, why anything is blocked, and the stop
 * for new trades. Every value comes from one backend payload; the browser computes
 * no health, no eligibility and no risk, because a screen that can reason about
 * whether trading is allowed is a screen that can be wrong about it.
 *
 * The one rule that shapes the layout: when the backend is unreachable, nothing
 * green is shown. Cached data is demoted to a muted "last known" line, never
 * presented as current state.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import {
  LIVE_OPERATIONS_KEY,
  dataAgeSeconds,
  useLiveOperations,
  useOperatorTradeHalt,
} from '../../hooks/useLiveOperations';
import { BrokerExposureCard } from './ops/BrokerExposureCard';
import { DeploymentCard } from './ops/DeploymentCard';
import { ModeBanner } from './ops/ModeBanner';
import { OperatorActions } from './ops/OperatorActions';
import { SafetyAdmissionCard } from './ops/SafetyAdmissionCard';
import { mono } from './ops/Card';
import { useIsSimulation } from '../../store/useViewStore';
import { SimDeniedNote } from '../sim/SimDeniedNote';
import { EmaTrendOperations } from './board/EmaTrendOperations';
import { useSimNowMs } from '../../hooks/useReplayStore';

export function LiveOperationsPanel() {
  const identity = useQuery<{ compatible: boolean; blockers: string[]; checkout_sha_at_start?: string; source_sha256_at_start?: string }>({
    queryKey: ['runtime-identity'],
    queryFn: () => api.get('/api/v1/ops/runtime-identity?frontend_sha=' + encodeURIComponent(import.meta.env.VITE_BUILD_SHA || 'UNKNOWN') + '&frontend_contract=' + encodeURIComponent(import.meta.env.VITE_API_CONTRACT || 'UNKNOWN')),
    refetchInterval: 60000,
  });
  const sim = useIsSimulation();
  const historical = useSimNowMs() !== null;
  const query = useLiveOperations();
  const controls = useOperatorTradeHalt();
  const qc = useQueryClient();

  const unreachable = query.isError;
  const payload = query.data;
  const age = dataAgeSeconds(payload?.generated_at);

  const refresh = () => qc.invalidateQueries({ queryKey: LIVE_OPERATIONS_KEY });
  const mutationError =
    (controls.stop.error as Error | null)?.message ??
    (controls.resume.error as Error | null)?.message ??
    null;

  return (
    <div data-testid="live-operations" style={{ padding: 10, minWidth: 0 }}>
      <div role={identity.data?.compatible ? 'status' : 'alert'} style={{ padding: 8, fontSize: 12 }}>
        Release compatibility: {identity.isError ? 'UNKNOWN — backend identity unavailable' : !identity.data ? 'checking' : identity.data.compatible ? 'matched' : identity.data.blockers?.join(', ') || 'UNKNOWN — incomplete identity response'}
        <div>Backend startup: {identity.data?.checkout_sha_at_start || 'unknown'} · Frontend: {import.meta.env.VITE_BUILD_SHA || 'unknown'}</div>
        <div>Backend source fingerprint: {identity.data?.source_sha256_at_start || 'unknown'}</div>
      </div>
      <ModeBanner mode={payload?.production_mode ?? 'UNKNOWN'} unreachable={unreachable} />

      {unreachable && (
        <div
          data-testid="ops-unreachable"
          role="alert"
          aria-live="assertive"
          style={{
            padding: '6px 10px',
            marginBottom: 10,
            border: '1px solid var(--k-err, #ef4444)',
            color: 'var(--k-err, #ef4444)',
            fontSize: 12,
          }}
        >
          STERLING UNREACHABLE — the values below, if any, are the last known state
          and are not current.
        </div>
      )}

      {!payload && !unreachable && (
        <div style={{ fontSize: 12, color: 'var(--k-muted, #8b93a7)' }}>Loading…</div>
      )}

      {payload && (
        <>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              gap: 10,
              marginBottom: 10,
              fontSize: 11,
              color: unreachable ? 'var(--k-muted, #8b93a7)' : 'inherit',
              opacity: unreachable ? 0.6 : 1,
            }}
          >
            <span>
              Next step: <strong>{payload.next_safe_action || 'none reported'}</strong>
            </span>
            <span style={mono}>
              {payload.runtime_sha ? `build ${payload.runtime_sha.slice(0, 12)} · ` : ''}
              {unreachable ? 'last known ' : ''}
              {age === null ? 'age UNKNOWN' : `${age}s ago`}
            </span>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 300px), 1fr))',
              gap: 10,
              opacity: unreachable ? 0.5 : 1,
            }}
          >
            <SafetyAdmissionCard safety={payload.safety} />
            <DeploymentCard deployment={payload.deployment} security={payload.security} />
            {sim ? (
              <SimDeniedNote what="Stopping and resuming new trades" why="They switch trading off for the whole app and the real account, so they work in the LIVE view only." />
            ) : (
              <OperatorActions
                operator={payload.operator}
                onStop={(reason) => controls.stop.mutate(reason)}
                onResume={(reason) => controls.resume.mutate(reason)}
                onRefresh={refresh}
                pending={controls.stop.isPending || controls.resume.isPending}
                error={mutationError}
              />
            )}
          </div>

          {payload.paper_strategies?.ema_trend && <div style={{ marginTop: 10 }}>
            <EmaTrendOperations campaigns={payload.paper_strategies.ema_trend.campaigns || []}
              supervision={unreachable ? undefined : payload.paper_strategies.ema_trend.supervision || undefined}
              unavailable={unreachable || payload.paper_strategies.ema_trend.status !== 'AVAILABLE'}
              paused={payload.paper_strategies.ema_trend.paused} showPause readOnly={historical} />
          </div>}
          <div style={{ marginTop: 10, opacity: unreachable ? 0.5 : 1 }}>
            <BrokerExposureCard broker={payload.broker} />
          </div>
        </>
      )}
    </div>
  );
}

export default LiveOperationsPanel;
