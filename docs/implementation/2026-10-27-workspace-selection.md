# Workspace selection and switching

Fresh Sterling sign-in opens the chooser before mounting a feature, including when sign-in starts at a saved feature URL. The enabled choices use Kite, Echo (Radio), and Space (TV), in that order. Feature identifiers and server authorization remain unchanged; missing grants do not become permissions through navigation.

Every workspace has Settings → Switch workspace to return to that chooser. The Kite settings drawer/display popover and media display-settings menu also include Switch workspace. Header Kite/Echo/Space text controls switch directly between permitted workspaces. Navigation asks before discarding an unapplied settings draft. Returning to the chooser unmounts the active feature.

Validation: TypeScript and 41 targeted UI tests passed. Six browser flows passed across the existing administration/login checks and new 390px/1440px chooser, settings-return, direct-switch and no-Kite-request checks. Browser provider responses are synthetic. Production rollout uses the existing atomic committed-frontend publisher, with no backend or tunnel restart.
