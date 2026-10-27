import React from 'react';
import { BORDER, MUTED, Section, TEXT } from './kiteSettingsPrimitives';
import { k } from '../../styles/kiteUI';

function Row({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="help-definition-row" style={{ display: 'grid', gridTemplateColumns: '110px minmax(0,1fr)', gap: 12, padding: '9px 0', borderTop: `1px solid ${BORDER}`, alignItems: 'start' }}>
      <div style={{ color: TEXT, fontSize: 12, fontWeight: 700 }}>{term}</div>
      <div style={{ color: MUTED, fontSize: 11.5, lineHeight: 1.55 }}>{children}</div>
    </div>
  );
}

/** Short operator reference for the four live strategies and the safety controls. */
export function HelpPane() {
  return (
    <div className="kite-settings-hub help-page" style={{ width: '100%', boxSizing: 'border-box', padding: '28px 30px 48px', background: 'var(--k-surface-sunken-2)', minHeight: '100%', fontFamily: k.fontFamily }}>
      <div style={{ maxWidth: 960, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div>
          <h2 style={{ margin: 0, color: 'var(--k-ink-1)', fontSize: 19, fontWeight: 750, letterSpacing: '-.02em' }}>Help</h2>
          <p style={{ margin: '6px 0 0', color: 'var(--k-ink-5)', fontSize: 12, lineHeight: 1.55, maxWidth: 720 }}>
            Sterling can trade your connected Zerodha account with real money when live execution and account readiness are enabled. Four strategies: SuperTrend, Snapback, Gamma Unwind and Price Rules.
          </p>
        </div>

        <section style={{ background: 'var(--k-bg)', border: `1px solid ${BORDER}`, borderRadius: 9, overflow: 'hidden', boxShadow: '0 1px 2px rgba(0,0,0,.025)' }}>
          <Section title="Using Sterling on your phone" description="Your same account and workspace." summary="Navigation · layout · installation" defaultOpen>
            <Row term="Navigation">Use Home, Signals, Watchlist and Terminal at the bottom. Menu opens Orders, Positions, Holdings, funds and all other pages.</Row>
            <Row term="Settings">Menu → Settings. Choose a section from the Settings section list, including Price Rules and Sterling Users.</Row>
            <Row term="Layout">Menu → Mobile app style for one screen at a time, or Full app style for the desktop docks. Your choice is saved on this device.</Row>
            <Row term="Install">Menu → Install app. Follow your browser’s confirmation using the HTTPS app address. The backend must keep running for live data and trading.</Row>
          </Section>
          <Section title="Arming automatic entry" description="Two switches, both required." summary="Master ALGO + strategy" defaultOpen>
            <Row term="Master ALGO">Trading Config, MANUAL or ALGO. While MANUAL, the three scan engines do not enter automatically. Price Rules uses its own explicit plan arming.</Row>
            <Row term="Strategy switch">Algo Trade, one switch per strategy. A switch only requests entries.</Row>
            <Row term="Always on">Risk, safety and broker checks can still refuse an entry. The reason shows on the row.</Row>
          </Section>

          <Section title="Safety controls" description="Always available in Trading Config." summary="Square-Off · Kill Switch · Safe mode">
            <Row term="Square-Off All">Cancels resting broker stops and exits every open Supertrend, Snapback, Gamma and Price Rules position through each strategy's own exit.</Row>
            <Row term="Kill Switch">Stops all new orders, turns ALGO off and exits open positions.</Row>
            <Row term="Safe mode">Blocks new entries after an unresolved order, missing stop or unreadable broker state. Exits and stops keep running. Resume trading needs a reason.</Row>
            <Row term="Daily loss">Entries halt after the daily loss limit is hit.</Row>
          </Section>

          <Section title="SuperTrend" description="Trend entries on 1-hour Heikin-Ashi." summary="Triple SuperTrend">
            <Row term="Entry">Buys an option when the three SuperTrend lines align. Contract choice, size and limits are set under Connect, SuperTrend.</Row>
            <Row term="Exit">The trailing stop (TSL) tightens as lines turn red. Stop mode: Broker (GTT at Zerodha), Monitor (Sterling watches) or Both.</Row>
            <Row term="Indicators">21 / 1, 14 / 2 and 7 / 3 on completed 1H Heikin-Ashi candles. A lower replay interval changes playback granularity, not the strategy timeframe.</Row>
            <Row term="Required">ALGO needs a daily loss limit and a broker stop.</Row>
            <Row term="Evidence">The retained studies found long ATM/OTM options losing after costs even with tighter exits. Deep ITM and futures had better modeled results, but these are research findings; actual premiums, margin, slippage and fresh out-of-sample results still need verification.</Row>
          </Section>

          <Section title="Snapback" description="Fades a stretched breakout with a bought option." summary="Mean reversion">
            <Row term="Entry">A close through the 20-session high, well above the 20-session mean, with the market weak. Buys a put. The opposite setup buys a call when fade-down is on.</Row>
            <Row term="Exit">Planned stop and target on the option premium, or the hold horizon.</Row>
            <Row term="Swing timing">Signals use daily closes: a 20-session extreme, 1.5 ATR stretch, and the configured market filter. Replay fills at the next session open. History rows are observations, never entry authority.</Row>
            <Row term="Scalp">Uses completed minute bars, quoted liquidity, a broker stop, premium target, hold limit and session square-off. Swing evidence does not validate scalp.</Row>
            <Row term="Modes">Each mode has its own signal timing and execution readiness. Read the selected mode’s broker, protection and risk blockers before arming; research results do not certify live execution.</Row>
            <Row term="Execute">The Trade column publishes a server-owned plan and arms its exact revision. Reconnect Kite or resolve the displayed risk/data reason if the button is unavailable.</Row>
            <Row term="Size">Risk percent of capital, capped by lots and cash.</Row>
          </Section>

          <Section title="Gamma Unwind" description="Open-interest unwind at a confirmed level." summary="Intraday options">
            <Row term="Entry">ENTRY-tier rows only. Watchlist rows never add exposure.</Row>
            <Row term="Exit">Live trades are intraday on contracts expiring today or tomorrow, closed by 15:20 IST. Needs a broker or both stop.</Row>
            <Row term="Size">One lot per entry, from the dated instrument master.</Row>
            <Row term="Historical">Recorded E1 events and one-minute historical reconstructions have separate provenance. Minute bars cannot prove live tick ordering or an executable fill. Historical rows cannot place orders.</Row>
            <Row term="Execute">ENTRY tier uses Gamma's own arm and protection path. Watch rows and unsupported swing protection stay unavailable.</Row>
          </Section>

          <Section title="Price Rules" description="Manual long index options and one-shot price conditions." summary="Connect → Price Rules, or the strategy strip shortcut">
            <Row term="Entry">Publish an exact call or put plan, then arm its displayed revision. Arming a manual plan requests entry on the next check; price rules wait for the condition.</Row>
            <Row term="Exit">Broker stop and optional target use confirmed fills. Partial exits use whole lots. Unknown orders or protection require reconciliation before another action.</Row>
            <Row term="Limits">Shares account safety and daily loss brakes. Naked short entries are unavailable. Escape requests owned exits outside input fields.</Row>
          </Section>

          <Section title="Replay and diagnostics" description="Review historical evidence and capture problems." summary="Replay · terminal capture · export">
            <Row term="Replay">Select SuperTrend, Snapback or Gamma Unwind. The signal boards follow the replay clock and contain only that session. Historical execution controls are disabled. Each operator sees their own session; this server has one replay slot, released by Stop then Clear. Existing positions retain reconciliation and exits during replay. Coverage notes distinguish missing data from a quiet strategy.</Row>
            <Row term="Evidence">Modelled premiums, minute proxies and live recorded signals are different evidence. Include fees, adverse fills, drawdown and untouched out-of-sample results before relying on a strategy for income.</Row>
            <Row term="Terminal">Use the capture icon beside Clear to include engine activity, frontend errors, network/stream events and backend logs. Filter source/severity and Download capture for a redacted JSON report.</Row>
            <Row term="Access">Global backend logs require an administrator. The bounded capture excludes request/response bodies, credentials and login query strings. Keep the capture open while reproducing a problem.</Row>
          </Section>
          <Section title="Reading the numbers" description="The same names on every screen." summary="Entry · LTP · SL · TSL · Target · Exit · P&L · Qty · Risk · Capital" >
            <Row term="Entry">Premium paid per unit.</Row>
            <Row term="LTP">Last traded price, with its age when stale.</Row>
            <Row term="SL / TSL">Initial stop, and the trailing stop once it moves.</Row>
            <Row term="Target / Exit">Target is the strategy plan; a dash means none was supplied. Exit shows the closing rule progress or confirmed exit price. Calculator edits are what-if estimates.</Row>
            <Row term="P&amp;L">Rupees, signed. Green gain, red loss.</Row>
            <Row term="Qty">Units, lots times lot size.</Row>
            <Row term="Risk">Rupees lost if the stop fills.</Row>
            <Row term="Capital">Rupees deployed at entry.</Row>
            <Row term="Status">Where the row is: watching, armed, running or ended.</Row>
          </Section>
        </section>
      </div>
    </div>
  );
}

export default HelpPane;
