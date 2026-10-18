import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';

/** Server-owned Price Rules entry admission, independent of existing position protection. */
export function usePriceRulesSwitch(simulation: boolean) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ['price-rules', 'enabled'],
    queryFn: () => api.get<{ enabled: boolean; revision: number }>('/api/v1/price-rules/enabled'),
    enabled: !simulation,
    refetchInterval: 3000,
  });
  const mutation = useMutation({
    mutationFn: (next: { enabled: boolean; expected_enabled: boolean }) =>
      api.post('/api/v1/price-rules/enabled', next),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['price-rules'] }),
  });
  const enabled = !simulation && query.data?.enabled === true;
  return {
    enabled,
    pending: mutation.isPending,
    error: mutation.error,
    toggle: !simulation && !query.isError && typeof query.data?.enabled === 'boolean'
      ? () => mutation.mutate({ enabled: !query.data!.enabled, expected_enabled: query.data!.enabled })
      : null,
  };
}
