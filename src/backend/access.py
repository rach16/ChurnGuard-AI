"""
Who may do what (2.6, ADR-0013 section 4), shared by every router.

``make_require(get_state)`` gives each router the same gate. An *action* (any
state change) must come from the site's own origin and re-checks the person
with Cognito at that moment, so a disabled account is refused on its next
click and a role removed in the console is gone. A *read* that needs a role
(the team list, admin only) checks the session's role without the round trip:
browsers send no Origin on same-origin GETs, and a read changes nothing.
"""

from __future__ import annotations

from typing import Callable

from fastapi import HTTPException, Request

from auth_routes import same_origin
from integrations.login import CognitoUnavailable, SessionEnded, actor as actor_of

# Higher includes lower.
ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}
ROLES = tuple(sorted(ROLE_RANK, key=ROLE_RANK.get, reverse=True))     # admin, operator, viewer


def role_rank(role) -> int:
    return ROLE_RANK.get(role or "", 0)


def make_require(get_state: Callable[[], object]):
    def require(min_role: str, *, action: bool = True):
        def dependency(request: Request) -> str:
            state = get_state()
            login = getattr(state, "login", None)
            session = getattr(request.state, "session", None)
            if login is None:
                raise HTTPException(status_code=404,
                                    detail="Actions need sign-in, which is not enabled on this deployment.")
            if session is None:
                raise HTTPException(status_code=401, detail="Sign in required.")
            if action:
                if not same_origin(request):
                    raise HTTPException(status_code=403, detail="This action must come from the site's own pages.")
                try:
                    session = login.recheck(session)
                except SessionEnded as e:
                    raise HTTPException(status_code=401, detail=str(e))
                except CognitoUnavailable as e:
                    raise HTTPException(status_code=503, detail=f"{e} Try again in a moment.")
                if session is None:
                    raise HTTPException(status_code=401, detail="Your sign-in ended. Sign in again.")
            if role_rank(session.role) < ROLE_RANK[min_role]:
                raise HTTPException(status_code=403, detail=f"This needs the {min_role} role.")
            request.state.session = session
            return actor_of(session)
        return dependency
    return require
