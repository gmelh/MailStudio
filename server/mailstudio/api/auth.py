"""/api/v1/auth: first-run setup, login, logout, and the current user."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Request, Response
from pydantic import EmailStr, Field

from .. import accounts
from ..config import Settings
from ..db import transaction
from ..deps import Db, UserPrincipal
from ..errors import ProblemError
from ..schemas import Strict, normalise_email

router = APIRouter(prefix="/auth", tags=["Login"])
Password = Annotated[str, Field(min_length=1, max_length=200)]
SessionCookie = Annotated[str | None, Cookie(alias=accounts.SESSION_COOKIE)]


class Setup(Strict):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    email: EmailStr
    password: Password


class Login(Strict):
    email: EmailStr
    password: Password


class PasswordChange(Strict):
    current_password: Password
    new_password: Password


def _set_cookie(response: Response, request: Request, token: str) -> None:
    app: Settings = request.app.state.settings
    response.set_cookie(
        accounts.SESSION_COOKIE,
        token,
        max_age=int(accounts.SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=app.secure_cookies,
        samesite="lax",
        path="/",
    )


def _me(conn: Db, user_id: str, csrf_token: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"user": accounts.public_user(row), "csrf_token": csrf_token}


@router.get("/status", summary="Is setup needed, and who is logged in?")
def status(conn: Db, request: Request, ms_session: SessionCookie = None) -> dict[str, Any]:
    """Public. The web app calls this first to choose between the setup, login and main screens."""
    session = accounts.load_session(conn, ms_session) if ms_session else None
    return {
        "setup_required": accounts.user_count(conn) == 0,
        "user": _me(conn, session.user_id, session.csrf_token) if session else None,
    }


@router.post("/setup", summary="Create the first account")
def setup(body: Setup, request: Request, response: Response, conn: Db) -> dict[str, Any]:
    """Only works while the install has no accounts at all."""
    email = normalise_email(body.email)
    problem = accounts.password_problem(body.password, email)
    if problem:
        raise ProblemError(422, "weak-password", "Choose a stronger password", problem)
    with transaction(conn):
        if accounts.user_count(conn):
            raise ProblemError(
                409, "already-set-up", "Already set up", "This install already has an account."
            )
        user_id = accounts.create_user(conn, email=email, name=body.name, password=body.password)
        token = accounts.start_session(conn, user_id)
    _set_cookie(response, request, token)
    session = accounts.load_session(conn, token)
    assert session is not None
    return _me(conn, user_id, session.csrf_token)


@router.post("/login", summary="Log in")
def login(body: Login, request: Request, response: Response, conn: Db) -> dict[str, Any]:
    throttle: accounts.LoginThrottle = request.app.state.login_throttle
    email = normalise_email(body.email)
    keys = (f"email:{email}", f"ip:{request.client.host if request.client else '-'}")
    if throttle.blocked(*keys):
        raise ProblemError(429, "too-many-attempts", "Too many attempts", "Wait 15 minutes and try again.")
    user = accounts.authenticate_password(conn, email, body.password)
    if user is None:
        throttle.failed(*keys)
        raise ProblemError(401, "bad-login", "Wrong email or password", None)
    throttle.succeeded(keys[0])
    with transaction(conn):
        token = accounts.start_session(conn, user["id"])
    _set_cookie(response, request, token)
    session = accounts.load_session(conn, token)
    assert session is not None
    return _me(conn, user["id"], session.csrf_token)


@router.post("/logout", status_code=204, summary="Log out")
def logout(
    request: Request, response: Response, conn: Db, _: UserPrincipal, ms_session: SessionCookie = None
) -> None:
    if ms_session:
        with transaction(conn):
            accounts.end_session(conn, ms_session)
    response.delete_cookie(accounts.SESSION_COOKIE, path="/")


@router.get("/me", summary="The logged-in user")
def me(conn: Db, person: UserPrincipal) -> dict[str, Any]:
    assert person.csrf_token is not None
    return _me(conn, person.id, person.csrf_token)


@router.post("/password", status_code=204, summary="Change your password")
def change_password(
    body: PasswordChange, conn: Db, person: UserPrincipal, ms_session: SessionCookie = None
) -> None:
    """Also logs out every other session of this account."""
    row = conn.execute("SELECT email, password_hash FROM users WHERE id = ?", (person.id,)).fetchone()
    if not accounts.check_password(body.current_password, row["password_hash"]):
        raise ProblemError(422, "bad-password", "Current password is wrong", None)
    problem = accounts.password_problem(body.new_password, row["email"])
    if problem:
        raise ProblemError(422, "weak-password", "Choose a stronger password", problem)
    with transaction(conn):
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (accounts.hash_password(body.new_password), person.id),
        )
        accounts.end_other_sessions(conn, person.id, ms_session)
