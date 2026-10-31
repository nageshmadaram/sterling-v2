"""Feature admission at HTTP boundaries; never gates protective worker exits."""
import re
from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection
from app.core.auth import UserContext, get_current_user
from app.services.auth import account_policy


def require_feature(user: UserContext, feature: str = 'kite'):
    try:
        return account_policy.require_feature(user.owner_uid or user.user_id, feature)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except account_policy.PolicyUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


def route_policy(path: str, method: str) -> str:
    """Unknown private API routes default to Kite; exceptions stay explicit."""
    if path == '/health' or path.startswith(('/health/', '/healthz')):
        return 'public'
    if method == 'GET' and path in ('/api/v1/health', '/api/v1/health/live', '/api/v1/health/ready'):
        return 'public'  # credential-free supervisor probes must survive policy rollout
    if path in ('/tma', '/api/v1/tma', '/tma/operator'):
        return 'public'
    # This POST is strictly a read-only journal lookup, so viewers with a
    # granted Kite workspace may inspect it. Every other unknown POST remains
    # a trading mutation under the default-deny role rule.
    if path == '/api/v1/compat/openalgo/orderstatus' and method == 'POST':
        return 'kite_read_only'
    if path.startswith(('/tma/', '/api/v1/tma/')):
        return 'telegram'
    if path.startswith('/api/v1/admin/'):
        return 'administration'
    if (path, method) in (('/api/v1/ops/runtime', 'GET'), ('/api/v1/snapback/prospective/health', 'GET')):
        return 'public'  # existing handlers retain their loopback/probe contracts
    if path.startswith(('/api/v1/auth/', '/api/v1/settings/')):
        return 'identity'
    if path.startswith('/api/v1/media/'):
        return 'media'  # bearer issuance or media-only cookie validation in its router
    if (path, method) in (('/api/v1/kite/callback', 'GET'), ('/api/v1/kite/postback', 'POST')):
        return 'provider'  # signed callbacks recheck grants; signed postbacks preserve reconciliation
    # Configuration routes have independent scoped action checks in their adapters.
    if method in ('POST', 'PUT', 'PATCH', 'DELETE') and re.fullmatch(r'/api/v1/kite/accounts(?:/[^/]+(?:/(?:credentials/reveal|login-credentials|activate|test))?)?', path):
        return 'configuration'
    if path.startswith('/api/v1/kite/telegram'):
        return 'configuration'
    if path.startswith('/api/v1/truedata/credentials') or path == '/api/v1/truedata/settings':
        return 'configuration'
    if path.startswith('/api/v1/'):
        return 'kite'
    return 'public'


async def enforce_feature_access(connection: HTTPConnection):
    if connection.scope['type'] != 'http':
        return  # WebSocket identity and each send/subscription have their own durable checks.
    request = connection
    policy = route_policy(request.url.path, request.method)
    if policy in ('kite', 'kite_read_only'):
        principal = await get_current_user(request)
        current = require_feature(principal)
        # A feature grant allows a viewer to inspect the workspace, not mutate
        # trading state. Unknown or imported roles fail closed on API writes.
        if policy == 'kite' and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            # get_current_user() already scopes an owner's Simulation request
            # to the virtual tenant and enforces SIM_VIEW_DENY_RULES for every
            # live broker/global mutation. Do not prohibit all simulation writes
            # here: that would disable the owner's isolated paper trading book.
            if not account_policy.may_mutate(current.role):
                raise HTTPException(403, 'Read-only viewer cannot mutate Kite state.')
    elif policy == 'configuration' and request.url.path.startswith('/api/v1/kite/accounts'):
        # Configuration grants are checked by the resource-specific endpoint;
        # independently refuse anonymous, inactive and read-only identities.
        principal = await get_current_user(request)
        if principal.view or principal.owner_uid:
            raise HTTPException(403, 'Simulation identity cannot mutate live broker configuration.')
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            try:
                state = account_policy.read(principal.user_id)
            except account_policy.PolicyUnavailable as exc:
                raise HTTPException(503, str(exc)) from None
            if not state.active or not account_policy.may_mutate(state.role):
                raise HTTPException(403, 'Read-only viewer cannot mutate broker configuration.')
    elif policy == 'administration':
        from app.core.configuration_access import require_configuration
        require_configuration(await get_current_user(request), 'brokers', administrator_only=True)
    elif policy == 'telegram':
        from app.services.notifications.telegram_webapp_auth import tma_user
        require_feature(await tma_user(request))
