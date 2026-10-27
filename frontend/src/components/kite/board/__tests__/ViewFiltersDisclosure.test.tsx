import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ViewFiltersDisclosure } from '../ViewFiltersDisclosure';

it('keeps view settings available behind an accessible disclosure', () => {
  const view = render(
    <ViewFiltersDisclosure>
      <button type="button">Columns</button>
      <button type="button">Modes</button>
    </ViewFiltersDisclosure>,
  );
  const toggle = screen.getByRole('button', { name: 'View filters' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(view.container.querySelector('.view-filter-items')).not.toHaveClass('is-open');
  fireEvent.click(toggle);
  expect(screen.getByRole('button', { name: 'Hide filters' })).toHaveAttribute('aria-expanded', 'true');
  expect(view.container.querySelector('.view-filter-items')).toHaveClass('is-open');
  expect(screen.getByRole('button', { name: 'Columns' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Modes' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Hide filters' }));
  expect(screen.getByRole('button', { name: 'View filters' })).toHaveAttribute('aria-expanded', 'false');
});
