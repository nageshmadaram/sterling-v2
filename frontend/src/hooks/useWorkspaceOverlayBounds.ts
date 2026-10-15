import {useEffect,useState} from 'react';
import {toLayoutPx} from '../styles/applyViewportScale';

/** Bounds between the mobile header and bottom navigation, in scaled layout pixels. */
export function useWorkspaceOverlayBounds(){
  const [bounds, setBounds] = useState({ top: 52, bottom: 80 });
  useEffect(() => {
    const header = document.querySelector('.sterling-header-row');
    const navigation = document.querySelector('.mobile-pane-tabs');
    const footer = document.querySelector('.sterling-workspace-footer');
    const measure = () => {
      const top = header?.getBoundingClientRect().bottom ?? 52;
      const navTop = navigation?.getBoundingClientRect().top;
      const bottomEdge = navTop != null && navTop > window.innerHeight / 2
        ? navTop : footer?.getBoundingClientRect().top ?? window.innerHeight;
      setBounds({ top: toLayoutPx(top), bottom: toLayoutPx(Math.max(0, window.innerHeight - bottomEdge)) });
    };
    measure();
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
    [header, navigation, footer].forEach(element => { if (element) observer?.observe(element); });
    window.addEventListener('resize', measure);
    window.visualViewport?.addEventListener('resize', measure);
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure); window.visualViewport?.removeEventListener('resize', measure); };
  }, []);
  return bounds;
}
