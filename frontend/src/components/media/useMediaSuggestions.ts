import {useEffect, useRef, useState} from 'react';
import {readMediaSuggestionResponse, type MediaKind} from './mediaBridge';
import type {MediaSuggestion} from './mediaSuggestions';

let requestSequence = 0;
type SuggestionState = {items: MediaSuggestion[]; status: 'idle' | 'loading' | 'ready' | 'timeout'};
const idle: SuggestionState = {items: [], status: 'idle'};

/** One cancellable request session per search, including same-query retries. */
export function useMediaSuggestions(kind: MediaKind, query: string, enabled: boolean) {
  const [state, setState] = useState<SuggestionState>(idle);
  const [attempt, setAttempt] = useState(0);
  const immediate = useRef(false);
  useEffect(() => {
    if (!enabled) {setState(idle); return;}
    const requestId = `${kind}-${++requestSequence}`;
    let active = true;
    setState({items: [], status: 'loading'});
    const receive = (event: Event) => {
      const response = readMediaSuggestionResponse((event as CustomEvent).detail);
      if (!active || response?.requestId !== requestId || response.query !== query) return;
      window.clearTimeout(timeout);
      setState({items: response.items, status: 'ready'});
    };
    const delay = query.trim() && !immediate.current ? 120 : 0;
    immediate.current = false;
    const timeout = window.setTimeout(() => {
      if (active) setState({items: [], status: 'timeout'});
    }, delay + 4000);
    window.addEventListener(`sterling-${kind}-suggestions`, receive);
    const request = window.setTimeout(() => {
      if (active) window.dispatchEvent(new CustomEvent(`sterling-${kind}-command`, {
        detail: {action: 'suggest', value: query, requestId},
      }));
    }, delay);
    return () => {
      active = false;
      window.clearTimeout(request); window.clearTimeout(timeout);
      window.removeEventListener(`sterling-${kind}-suggestions`, receive);
    };
  }, [kind, query, enabled, attempt]);
  return {...(enabled ? state : idle), retry: () => {immediate.current = true;setAttempt(value => value + 1);}};
}
