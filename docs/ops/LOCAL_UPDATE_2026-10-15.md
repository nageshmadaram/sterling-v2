# Local update — 15 October 2026

This update includes the retained Gamma/PCR corrections, mobile feed recovery, account-session and preference fixes, Mini App sign-in, mobile overlay navigation, profile/theme controls and shared strategy-row presentation.

Config preferences remain account-persistent. Current page/section/strategy restore from browser session state; temporary overlays and filters do not restore. Existing saved user data was preserved. Remembered Sterling refresh credentials validate signatures and account token versions; access credentials remain short-lived. Kite daily login is separate. The Mini App offers a Sterling login form when Telegram authentication is unavailable.

Search and order review fill the mobile workspace between header and navigation. Search closes when a chart or ticket opens; close/Back returns to the preceding pane. Signal groups start with a single mobile contract summary; expanded rows retain the selected columns. The removed Direction column no longer renders. Legacy visibility flags can be recovered from the Columns picker. Profile sizing, favourites theme menu and strategy controls share the existing visual tokens.

The action-colour experiment described in GAMMA_PCR_UPDATE_2026-10-12.md was subsequently reverted for the PCR Desk at the operator's request. Ticker PCR action colours were retained. PCR historical/live context is behind its information icon; PCR and Flow Tape use full-page vertical scrolling.

Validation before publication: 176 focused frontend tests passed across strategy boards, columns, theme menu, workspace navigation, ticker, login, Back stack, tick freshness and remembered authentication. 49 affected backend tests passed across Gamma alerts/reference gates, PCR recovery, Mini App, auth and UI preferences. Production frontend build and TypeScript checks passed; git diff --check passed. Pending-file secret-pattern and private-file-type scan found no matches.

Runtime checks covered phone-width profile/theme menus, collapsed and expanded signals, no horizontal document overflow, foreground search/chart/order staging and return to the preceding page. No funded order was submitted. No synthetic Telegram alert or remote-access publication was sent. Actual phone OS eviction and funded broker fills are not certified by these checks; profitability is not established.

Credentials, environment files, encryption keys, databases and runtime broker state are excluded from this source update. Local-only configuration and existing trade/risk settings were preserved. The graph tools were unavailable; source-review fallback was used.
