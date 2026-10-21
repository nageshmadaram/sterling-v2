import React from 'react';
import { create } from 'zustand';
import type { EngineConfigModel } from '../../../types/kiteEngine';
import { SettingsDraftBar } from './ConfigPrimitives';
import { useUnsavedDraftGuard } from './unsavedDraftGuard';
import { useConfigPatch } from './useConfigPatch';
import { useEngineConfig, usePatchEngineConfig } from '../../../hooks/useSterlingKiteEngine';

/**
 * One pending draft of the SuperTrend engine config, shared by every Connect
 * panel that edits it (Trading Config, Manual Trade, Algo Trade).
 *
 * Those panels used to write each click straight to the backend, while the
 * engine pages staged edits behind an Apply/Discard bar. Same config, two
 * behaviours. Now every control stages here and the bar commits once.
 */
interface DraftState {
  draft: Partial<EngineConfigModel>;
  stage: (values: Partial<EngineConfigModel>) => void;
  clear: () => void;
}

export const useEngineConfigDraft = create<DraftState>((set) => ({
  draft: {},
  stage: (values) => set((s) => ({ draft: { ...s.draft, ...values } })),
  clear: () => set({ draft: {} }),
}));

/** Drop staged keys whose value already equals the saved config. */
function pending(draft: Partial<EngineConfigModel>, saved: EngineConfigModel | undefined) {
  if (!saved) return draft;
  const out: Partial<EngineConfigModel> = {};
  for (const [key, value] of Object.entries(draft) as [keyof EngineConfigModel, unknown][]) {
    if (JSON.stringify(saved[key]) !== JSON.stringify(value)) (out as Record<string, unknown>)[key] = value;
  }
  return out;
}

/**
 * Same shape as {@link useConfigPatch}, but `patch` stages instead of saving and
 * `cfg` is the saved config with the draft laid over it, so controls show what
 * Apply will write.
 */
export function useStagedConfigPatch() {
  const { data: saved } = useEngineConfig();
  const saving = usePatchEngineConfig().isPending;
  const draft = useEngineConfigDraft((s) => s.draft);
  const stage = useEngineConfigDraft((s) => s.stage);
  const cfg = React.useMemo(
    () => (saved ? ({ ...saved, ...draft } as EngineConfigModel) : saved),
    [saved, draft],
  );
  const patch = (values: Partial<EngineConfigModel>, ..._rest: unknown[]) => stage(values);
  return { cfg, patch, saving, rescanning: false };
}

/** The Apply/Discard bar for the shared engine-config draft. Mounted once, by ConnectPane. */
export function EngineConfigDraftBar() {
  const live = useConfigPatch();
  const draft = useEngineConfigDraft((s) => s.draft);
  const clear = useEngineConfigDraft((s) => s.clear);
  const changes = pending(draft, live.cfg);
  const dirty = Object.keys(changes).length > 0;
  useUnsavedDraftGuard('engine-config', dirty);
  // Unmount discards: a draft no page shows cannot be applied by anyone.
  React.useEffect(() => () => clear(), [clear]);
  return (
    <SettingsDraftBar
      dirty={dirty}
      saving={live.saving}
      onApply={() => live.patch(changes, undefined, undefined, { onSuccess: clear })}
      onDiscard={clear}
    />
  );
}
