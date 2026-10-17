import { describe, expect, it } from 'vitest';
import { groupPrimaryNavigation } from '../navigationDensity';

describe('compact workspace navigation', () => {
  it('preserves the operator-customized order, without hiding destinations', () => {
    const choices = [
      {id:'journal'}, {id:'positions'}, {id:'dashboard'},
      {id:'connect'}, {id:'orders'}, {id:'pcr'}, {id:'help'},
    ];
    const { primary, overflow } = groupPrimaryNavigation(choices);
    expect(primary.map(x=>x.id)).toEqual(['positions','dashboard','orders','pcr']);
    expect(overflow.map(x=>x.id)).toEqual(['journal','connect','help']);
    expect([...primary, ...overflow]).toHaveLength(choices.length);
  });
  it('does not create a second menu for a filtered all-primary navigation', () => {
    expect(groupPrimaryNavigation([{id:'dashboard'}, {id:'orders'}]).overflow).toEqual([]);
  });
});
