import React from 'react';
import {it,expect,vi,afterEach} from 'vitest';
import {render,screen,fireEvent,cleanup} from '@testing-library/react';
import {KiteActionButtons} from '../KiteActionButtons';
afterEach(cleanup);
it('offers a direct ticker action without opening an order',()=>{
 const pin=vi.fn(),buy=vi.fn();render(<KiteActionButtons onTicker={pin} onBuy={buy}/>);
 fireEvent.click(screen.getByRole('button',{name:'Add to ticker'}));
 expect(pin).toHaveBeenCalledOnce();expect(buy).not.toHaveBeenCalled();
});
it('shows the current pin and offers removal',()=>{
 render(<KiteActionButtons onTicker={()=>{}} tickerPinned/>);
 expect(screen.getByRole('button',{name:'Remove from ticker'}).getAttribute('aria-pressed')).toBe('true');
});
