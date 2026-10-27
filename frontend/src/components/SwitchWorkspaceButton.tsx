/** Return to the authenticated chooser without selecting or mounting a feature. */
export function SwitchWorkspaceButton({ onSwitch, className, role }: {
  onSwitch?: () => void; className?: string; role?: 'menuitem';
}) {
  return <button type="button" className={className} role={role} onClick={() => {
    onSwitch?.();
    window.dispatchEvent(new CustomEvent('sterling-navigate', { detail: 'home' }));
  }}>Switch workspace</button>;
}
