import {readMediaSuggestions, type MediaSuggestion} from './mediaSuggestions';

export type MediaKind = 'tv' | 'radio';
export type MediaCommand = {
  action: 'theme' | 'search' | 'more' | 'startup' | 'suggest' | 'select-suggestion' | 'play-search' | 'collapse' | 'minimize' | 'sources';
  value?: string;
  requestId?: string;
};
export type MediaSuggestionResponse = {query: string; items: MediaSuggestion[]; requestId?: string};
const common = new Set(['theme','search','more','startup','sources','suggest','select-suggestion']);
const validRequestId = (value: unknown): value is string => typeof value === 'string' && /^[a-zA-Z0-9-]{1,64}$/.test(value);

/** Copy only bounded display/player fields across the opaque iframe boundary. */
export function readMediaCommand(kind: MediaKind, value: unknown): MediaCommand | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>;
  if (typeof data.action !== 'string' || !(common.has(data.action) ||
      (kind === 'tv' ? ['play-search','collapse'] : ['minimize']).includes(data.action))) return null;
  const result: MediaCommand = {action: data.action as MediaCommand['action']};
  if (['theme','search','suggest','select-suggestion'].includes(result.action)) {
    const limit = result.action === 'select-suggestion' ? 4096 : 200;
    if (typeof data.value !== 'string' || data.value.length > limit) return null;
    if (result.action === 'theme' && !['spotify','youtube','netflix'].includes(data.value)) return null;
    if (result.action === 'select-suggestion' && !data.value.trim()) return null;
    result.value = data.value;
  }
  if (result.action === 'suggest' && data.requestId !== undefined) {
    if (!validRequestId(data.requestId)) return null;
    result.requestId = data.requestId;
  }
  return result;
}

export function readMediaSuggestionResponse(value: unknown): MediaSuggestionResponse | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>;
  if (typeof data.query !== 'string' || data.query.length > 200) return null;
  if (data.requestId !== undefined && !validRequestId(data.requestId)) return null;
  return {query: data.query, items: readMediaSuggestions(data.items),
    ...(data.requestId === undefined ? {} : {requestId: data.requestId as string})};
}
