import { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { InitialAccessFields, newAccess } from './InitialAccess';

afterEach(cleanup);

function Form() {
  const [draft, setDraft] = useState(newAccess());
  return <div>
    <InitialAccessFields value={draft} onChange={setDraft} />
    <output data-testid="access">{JSON.stringify(draft)}</output>
  </div>;
}

it('defaults all workspace cards visible but without grants', () => {
  const policy = newAccess();
  expect(policy.features).toEqual({kite:false,radio:false,tv:false});
  expect(policy.visibility).toEqual({kite:true,radio:true,tv:true});
});

it('allows admin to hide ungranted Kite without hiding Echo and Space', () => {
  render(<Form />);
  fireEvent.click(screen.getByRole('checkbox', {name:'Show kite in workspace chooser'}));
  const result = JSON.parse(screen.getByTestId('access').textContent!);
  expect(result.visibility).toEqual({kite:false,radio:true,tv:true});
  expect(result.features).toEqual({kite:false,radio:false,tv:false});
});

it('does not grant access when toggling only menu visibility', () => {
  render(<Form />);
  fireEvent.click(screen.getByRole('checkbox', {name:'Show kite in workspace chooser'}));
  fireEvent.click(screen.getByRole('checkbox', {name:'Allow tv access'}));
  const result = JSON.parse(screen.getByTestId('access').textContent!);
  expect(result.features).toEqual({kite:false,radio:false,tv:true});
  expect(result.visibility).toEqual({kite:false,radio:true,tv:true});
});
