import { useId, useRef, useState } from 'react';
import type { User } from '../kite/SterlingUsersPanel';

/** A single searchable account picker, with keyboard and touch selection. */
export function UserCombobox({ users, value, onChange, label = 'Sterling user', required = false }: {
  users: User[]; value: string; onChange: (id: string) => void; label?: string; required?: boolean;
}) {
  const id = useId(), input = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false), [query, setQuery] = useState(''), [active, setActive] = useState(0);
  const selected = users.find(user => user.id === value);
  const matches = users.filter(user => user.username.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  function choose(user: User) { onChange(user.id); setQuery(''); setOpen(false); input.current?.focus(); }
  return <div className="admin-user-picker" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <label htmlFor={id}>{label}</label><div className="admin-user-picker-field">
      <input id={id} ref={input} className="bac-input" role="combobox" aria-autocomplete="list" aria-expanded={open}
        aria-controls={`${id}-list`} aria-activedescendant={open && matches[active] ? `${id}-${matches[active].id}` : undefined}
        autoComplete="off" placeholder="Search and select a user" required={required} value={open ? query : selected?.username || ''}
        onFocus={() => { setQuery(''); setActive(0); setOpen(true); }} onClick={() => setOpen(true)}
        onChange={event => { setQuery(event.target.value); setActive(0); setOpen(true); }}
        onKeyDown={event => {
          if (event.key === 'Escape') { event.preventDefault(); setOpen(false); }
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setOpen(true); setActive(index => matches.length ? (index + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length : 0); }
          if (event.key === 'Enter' && open) { event.preventDefault(); if (matches[active]) choose(matches[active]); }
        }} /><span aria-hidden>⌕</span>
    </div>
    {open && <div id={`${id}-list`} role="listbox" aria-label={`${label} results`} className="admin-user-results">
      {matches.map((user, index) => <button type="button" role="option" aria-selected={value === user.id} id={`${id}-${user.id}`} key={user.id}
        className={active === index ? 'is-active' : ''} onPointerDown={event => event.preventDefault()} onClick={() => choose(user)}>
        <span className="admin-user-initial">{user.username.slice(0, 2).toUpperCase()}</span><span><strong>{user.username}</strong><small>{user.role === 'admin' ? 'Administrator' : user.active ? 'Sterling user' : 'Suspended'}</small></span><span aria-hidden>{value === user.id ? '✓' : ''}</span>
      </button>)}{!matches.length && <p>No users match “{query}”.</p>}
    </div>}
  </div>;
}
