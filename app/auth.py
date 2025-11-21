# app/auth.py
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.status import HTTP_303_SEE_OTHER
from sqlmodel import Session, select
from passlib.context import CryptContext
from sqlmodel import SQLModel, Field

# NOTE: this User model is minimal — if you already have User model in app/main.py or app/models.py
# you can remove this and import it instead.
class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    hashed_password: str
    is_admin: bool = False
    farm_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

pwdctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
router = APIRouter()

# session helper - re-use the same get_session used in your main.py; adjust import if you placed it elsewhere.
from app.main import get_session, templates  # import your get_session generator and templates

def verify_password(plain: str, hashed: str) -> bool:
    return pwdctx.verify(plain, hashed)

def current_user(request: Request):
    """Return username stored in session or None."""
    sess = request.session
    username = sess.get("username")
    if not username:
        return None
    # optionally you can fetch full user from DB
    with next(get_session()) as db:
        user = db.exec(select(User).where(User.username == username)).first()
        return user

@router.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    # show login form
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@router.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...), session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid username or password"})
    if not verify_password(password, user.hashed_password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid username or password"})
    # set session
    request.session["username"] = user.username
    request.session["is_admin"] = bool(user.is_admin)
    # Redirect to dashboard
    return RedirectResponse(url="/dashboard", status_code=HTTP_303_SEE_OTHER)

@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

# helper dependency for routes that require login
def require_user(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return u

# helper dependency for admin-only
def require_admin(request: Request):
    u = current_user(request)
    if not u or not getattr(u, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin only")
    return u
