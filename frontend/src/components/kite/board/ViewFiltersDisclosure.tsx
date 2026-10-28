import React from 'react';

/** Fold read-only board display filters into one mobile control.
 * Trading/scan actions are deliberately NOT children of this disclosure. */
export function ViewFiltersDisclosure({ children }: { children: React.ReactNode }) {
  const [expanded, setExpanded] = React.useState(false);
  const id = React.useId();
  return <>
    <button type="button" className="view-filter-toggle sb-tool"
      aria-controls={id} aria-expanded={expanded}
      onClick={() => setExpanded(previous => !previous)}>
      {expanded ? 'Hide filters' : 'View filters'}
    </button>
    <div id={id} className={`view-filter-items${expanded ? ' is-open' : ''}`}>
      {children}
    </div>
  </>;
}
