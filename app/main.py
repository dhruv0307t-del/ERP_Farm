# app/main.py
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional, List
import sqlite3
import os
import shutil

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from starlette.middleware.sessions import SessionMiddleware

from sqlmodel import (
    SQLModel,
    Field,
    Session,
    select,
    create_engine,
)
from sqlalchemy.exc import IntegrityError

from passlib.context import CryptContext
from passlib.exc import UnknownHashError

# ---------------------------------------------------------------------
# App, DB, templates
# ---------------------------------------------------------------------

app = FastAPI(title="Cattle ERP with Auth")

# mount static files so templates can reference /static/...
# ensure you have a "static/" directory at repo root with images/css/js you need
app.mount("/static", StaticFiles(directory="static"), name="static")

# session middleware (simple signed cookie) - read secret from env if present
SESSION_SECRET = os.getenv("SESSION_SECRET", "replace-with-a-long-random-secret")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
)

# DB selection: prefer DATABASE_URL (for production e.g. Render Postgres), fallback to sqlite file
DB_PATH = "erp.db"
DB_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# create engine; when sqlite, pass check_same_thread=False for FastAPI multi-threaded use
if DB_URL.startswith("sqlite"):
    engine = create_engine(DB_URL, echo=False, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DB_URL, echo=False)

templates = Jinja2Templates(directory="templates")
templates.env.globals["datetime"] = datetime

# password hashing (pbkdf2 to avoid bcrypt/backend issues on some hosts)
pwdctx = CryptContext(
    schemes=["pbkdf2_sha256"],
    default="pbkdf2_sha256",
    deprecated="auto",
)

DEFAULT_GESTATION_DAYS = 283  # cows (adjust per breed later)


# ---------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------


class Breed(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str


class Farm(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    location: Optional[str] = None


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    hashed_password: str
    full_name: Optional[str] = None
    is_admin: bool = Field(default=False)
    farm_id: Optional[int] = Field(default=None, foreign_key="farm.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Animal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tag_no: str = Field(index=True, unique=True)
    sex: str  # "F" or "M"
    birthdate: date
    breed_id: Optional[int] = Field(default=None, foreign_key="breed.id")
    farm_id: Optional[int] = Field(default=None, foreign_key="farm.id")

    # Extended fields requested
    cattle_type: str = Field(default="Cow")  # Cow / Buffalo / etc.
    mother_tag_no: Optional[str] = Field(default=None)
    lactating: bool = Field(default=False)
    pregnant: bool = Field(default=False)
    vaccinated: bool = Field(default=False)
    health: Optional[str] = Field(default=None)
    weight: float = Field(default=0.0)
    reproductions: int = Field(default=0)

    created_at: datetime = Field(default_factory=datetime.utcnow)


class MilkYieldDaily(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    animal_id: int = Field(foreign_key="animal.id", index=True)
    entry_date: date = Field(index=True)
    am_liters: float = 0.0
    pm_liters: float = 0.0
    total_liters: float = 0.0


class BreedingType(str, Enum):
    Heat = "Heat"
    AI = "AI"
    NaturalService = "NaturalService"
    PDPositive = "PDPositive"
    PDNegative = "PDNegative"


class BreedingEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    animal_id: int = Field(foreign_key="animal.id", index=True)
    event_type: BreedingType
    event_date: date
    notes: Optional[str] = None


class Gestation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    animal_id: int = Field(foreign_key="animal.id", index=True)
    service_date: date  # when conception assumed
    predicted_calving_date: date
    actual_calving_date: Optional[date] = None
    notes: Optional[str] = None


class VaccinationReminder(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    animal_id: int = Field(foreign_key="animal.id", index=True)
    reminder_date: date
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------
# DB helpers & migration
# ---------------------------------------------------------------------


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def _table_has_column(db_path: str, table: str, column: str) -> bool:
    if not os.path.exists(db_path):
        return False
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info('{table}')")
    cols = [r[1] for r in cur.fetchall()]
    conn.close()
    return column in cols


def migrate_add_animal_columns(db_path: str = DB_PATH) -> None:
    """
    Add missing animal columns to existing DB (with backup).
    Adds:
      - cattle_type (TEXT)
      - mother_tag_no (TEXT)
      - lactating (BOOLEAN default 0)
      - pregnant (BOOLEAN default 0)
      - vaccinated (BOOLEAN default 0)
      - health (TEXT)
      - weight (REAL default 0.0)
      - reproductions (INTEGER default 0)
    """
    # only runs for sqlite fallback DB file
    if not os.path.exists(db_path):
        return

    needed = []
    if not _table_has_column(db_path, "animal", "cattle_type"):
        needed.append(("cattle_type", "TEXT", "DEFAULT 'Cow'"))
    if not _table_has_column(db_path, "animal", "mother_tag_no"):
        needed.append(("mother_tag_no", "TEXT", ""))
    if not _table_has_column(db_path, "animal", "lactating"):
        needed.append(("lactating", "BOOLEAN", "DEFAULT 0"))
    if not _table_has_column(db_path, "animal", "pregnant"):
        needed.append(("pregnant", "BOOLEAN", "DEFAULT 0"))
    if not _table_has_column(db_path, "animal", "vaccinated"):
        needed.append(("vaccinated", "BOOLEAN", "DEFAULT 0"))
    if not _table_has_column(db_path, "animal", "health"):
        needed.append(("health", "TEXT", ""))
    if not _table_has_column(db_path, "animal", "weight"):
        needed.append(("weight", "REAL", "DEFAULT 0.0"))
    if not _table_has_column(db_path, "animal", "reproductions"):
        needed.append(("reproductions", "INTEGER", "DEFAULT 0"))

    if not needed:
        return

    # backup before altering
    bak = f"{db_path}.bak.{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    shutil.copyfile(db_path, bak)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    for col, typ, default in needed:
        sql = f"ALTER TABLE animal ADD COLUMN {col} {typ} {default}"
        cur.execute(sql)
    conn.commit()
    conn.close()


def get_session():
    with Session(engine) as session:
        yield session


def hash_password(plain: str) -> str:
    return pwdctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwdctx.verify(plain, hashed)
    except UnknownHashError:
        return False


def init_db() -> None:
    """Create tables, run migrations, create default farm/admin if missing."""
    create_db_and_tables()
    # run sqlite-only migration (safe no-op for Postgres)
    # only attempt when using local sqlite file DB
    if DB_URL.startswith("sqlite"):
        migrate_add_animal_columns(DB_PATH)

    with Session(engine) as session:
        # default farm
        farm = session.exec(select(Farm)).first()
        if not farm:
            farm = Farm(name="Default Farm", location="Unknown")
            session.add(farm)
            session.commit()
            session.refresh(farm)

        # default admin user (if missing)
        admin = session.exec(select(User).where(User.username == "admin")).first()
        if not admin:
            admin = User(
                username="admin",
                full_name="Super Admin",
                hashed_password=hash_password("adminpass"),
                is_admin=True,
                farm_id=farm.id,
            )
            session.add(admin)
            session.commit()


@app.on_event("startup")
def on_startup():
    init_db()


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------


def form_bool(value: Optional[str]) -> bool:
    """
    Convert common form values to bool:
    - checkbox sends "on" when checked
    - may receive "yes"/"true"/"1"
    """
    if value is None:
        return False
    v = str(value).lower()
    return v in ("on", "yes", "true", "1")


def get_current_user(request: Request, session: Session) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = session.get(User, user_id)
    if not user:
        request.session.clear()
        return None
    return user


def require_login(request: Request, db: Session) -> User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=302, detail="Login required")
    return user


# ---------------------------------------------------------------------
# Auth routes (login/logout/signup)
# ---------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None},
    )


@app.post("/login", response_class=HTMLResponse)
def do_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    user = session.exec(select(User).where(User.username == username)).first()
    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid username or password.",
            },
            status_code=400,
        )

    request.session["user_id"] = user.id
    request.session["is_admin"] = user.is_admin
    request.session["farm_id"] = user.farm_id

    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/home", status_code=302)


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request})


@app.post("/signup", response_class=HTMLResponse)
def signup(
    request: Request,
    farm_name: str = Form(...),
    farm_location: str = Form(""),
    full_name: str = Form(""),
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    existing = session.exec(select(User).where(User.username == username)).first()
    if existing:
        return templates.TemplateResponse(
            "signup.html",
            {
                "request": request,
                "error": "Username already taken, please choose another.",
                "form": {
                    "farm_name": farm_name,
                    "farm_location": farm_location,
                    "full_name": full_name,
                    "username": username,
                },
            },
            status_code=400,
        )

    farm = Farm(name=farm_name, location=farm_location or None)
    session.add(farm)
    session.commit()
    session.refresh(farm)

    user = User(
        username=username,
        full_name=full_name or username,
        hashed_password=hash_password(password),
        farm_id=farm.id,
        is_admin=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["farm_id"] = user.farm_id
    request.session["is_admin"] = user.is_admin

    return RedirectResponse("/dashboard", status_code=303)


# ---------------------------------------------------------------------
# Public home/landing (pass user so template can show/hide login/signup)
# ---------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def root_redirect(request: Request, session: Session = Depends(get_session)):
    user = get_current_user(request, session)
    # optional hero counts limited to user's farm
    animals_count = None
    milk_entries_count = None
    gestations_count = None
    if user:
        stmt = select(Animal).where(Animal.farm_id == user.farm_id) if user.farm_id else select(Animal)
        animals_count = len(session.exec(stmt).all())

        stmt_milk = select(MilkYieldDaily).where(MilkYieldDaily.entry_date == date.today())
        milk_rows = session.exec(stmt_milk).all()
        if user.farm_id:
            milk_entries_count = sum(1 for m in milk_rows if (session.get(Animal, m.animal_id) and session.get(Animal, m.animal_id).farm_id == user.farm_id))
        else:
            milk_entries_count = len(milk_rows)

        gest_q = select(Gestation).where((Gestation.predicted_calving_date >= date.today()) & (Gestation.actual_calving_date.is_(None)))
        gest_list = session.exec(gest_q).all()
        if user.farm_id:
            gestations_count = sum(1 for g in gest_list if (session.get(Animal, g.animal_id) and session.get(Animal, g.animal_id).farm_id == user.farm_id))
        else:
            gestations_count = len(gest_list)

    return templates.TemplateResponse("home.html", {"request": request, "user": user, "animals_count": animals_count, "milk_entries_count": milk_entries_count, "gestations_count": gestations_count})


@app.get("/home", response_class=HTMLResponse)
def home(request: Request, session: Session = Depends(get_session)):
    user = get_current_user(request, session)

    animals_count = None
    milk_entries_count = None
    gestations_count = None
    if user:
        stmt = select(Animal).where(Animal.farm_id == user.farm_id) if user.farm_id else select(Animal)
        animals_count = len(session.exec(stmt).all())

        stmt_milk = select(MilkYieldDaily).where(MilkYieldDaily.entry_date == date.today())
        milk_rows = session.exec(stmt_milk).all()
        if user.farm_id:
            milk_entries_count = sum(1 for m in milk_rows if (session.get(Animal, m.animal_id) and session.get(Animal, m.animal_id).farm_id == user.farm_id))
        else:
            milk_entries_count = len(milk_rows)

        gest_q = select(Gestation).where((Gestation.predicted_calving_date >= date.today()) & (Gestation.actual_calving_date.is_(None)))
        gest_list = session.exec(gest_q).all()
        if user.farm_id:
            gestations_count = sum(1 for g in gest_list if (session.get(Animal, g.animal_id) and session.get(Animal, g.animal_id).farm_id == user.farm_id))
        else:
            gestations_count = len(gest_list)

    return templates.TemplateResponse("home.html", {"request": request, "user": user, "animals_count": animals_count, "milk_entries_count": milk_entries_count, "gestations_count": gestations_count})


# ---------------------------------------------------------------------
# Animals / CRUD / search / details / milk / breeding / vaccination reminders
# ---------------------------------------------------------------------


@app.get("/animals", response_class=HTMLResponse)
def animals_list(
    request: Request,
    q: Optional[str] = None,
    session: Session = Depends(get_session),
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    stmt = select(Animal)
    if user.farm_id and not user.is_admin:
        stmt = stmt.where(Animal.farm_id == user.farm_id)

    animals_all = session.exec(stmt.order_by(Animal.id)).all()

    # simple search across tag_no, mother_tag_no, breed name
    if q:
        ql = q.lower()
        filtered = []
        for a in animals_all:
            breed = session.get(Breed, a.breed_id) if a.breed_id else None
            if (
                ql in (a.tag_no or "").lower()
                or ql in (a.mother_tag_no or "").lower()
                or (breed and ql in breed.name.lower())
            ):
                filtered.append(a)
        animals = filtered
    else:
        animals = animals_all

    return templates.TemplateResponse(
        "animals.html",
        {
            "request": request,
            "animals": animals,
            "user": user,
            "q": q or "",
        },
    )


@app.post("/animals", response_class=HTMLResponse)
def animals_create(
    request: Request,
    tag_no: str = Form(...),
    sex: str = Form(...),
    birthdate: date = Form(...),
    breed_name: Optional[str] = Form(None),
    cattle_type: str = Form("Cow"),
    mother_tag_no: Optional[str] = Form(None),
    lactating: Optional[str] = Form(None),
    pregnant: Optional[str] = Form(None),
    vaccinated: Optional[str] = Form(None),
    health: Optional[str] = Form(None),
    weight: float = Form(0.0),
    reproductions: int = Form(0),
    session: Session = Depends(get_session),
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    breed_id = None
    if breed_name:
        breed = session.exec(select(Breed).where(Breed.name == breed_name)).first()
        if not breed:
            breed = Breed(name=breed_name)
            session.add(breed)
            session.commit()
            session.refresh(breed)
        breed_id = breed.id

    animal = Animal(
        tag_no=tag_no,
        sex=sex,
        birthdate=birthdate,
        breed_id=breed_id,
        farm_id=user.farm_id,
        cattle_type=cattle_type or "Cow",
        mother_tag_no=mother_tag_no or None,
        lactating=form_bool(lactating),
        pregnant=form_bool(pregnant),
        vaccinated=form_bool(vaccinated),
        health=health or None,
        weight=weight or 0.0,
        reproductions=int(reproductions or 0),
    )
    session.add(animal)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=400, detail="Tag already exists")

    return RedirectResponse(url="/animals", status_code=302)


@app.get("/animals/{animal_id}", response_class=HTMLResponse)
def animal_detail(
    request: Request,
    animal_id: int,
    session: Session = Depends(get_session),
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    animal = session.get(Animal, animal_id)
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    if user.farm_id and not user.is_admin and animal.farm_id != user.farm_id:
        raise HTTPException(status_code=403, detail="Not allowed")

    breeding_events = session.exec(
        select(BreedingEvent)
        .where(BreedingEvent.animal_id == animal_id)
        .order_by(BreedingEvent.event_date.desc())
    ).all()

    gestation = session.exec(
        select(Gestation)
        .where(Gestation.animal_id == animal_id)
        .order_by(Gestation.service_date.desc())
    ).first()

    milk_entries = session.exec(
        select(MilkYieldDaily)
        .where(MilkYieldDaily.animal_id == animal_id)
        .order_by(MilkYieldDaily.entry_date.desc())
    ).all()

    vac_reminders = session.exec(
        select(VaccinationReminder)
        .where(VaccinationReminder.animal_id == animal_id)
        .order_by(VaccinationReminder.reminder_date.desc())
    ).all()

    return templates.TemplateResponse(
        "animal_detail.html",
        {
            "request": request,
            "animal": animal,
            "breeding_events": breeding_events,
            "gestation": gestation,
            "milk_entries": milk_entries,
            "vac_reminders": vac_reminders,
            "DEFAULT_GESTATION_DAYS": DEFAULT_GESTATION_DAYS,
            "today": date.today(),
            "user": user,
        },
    )


@app.get("/animals/{animal_id}/edit", response_class=HTMLResponse)
def animal_edit_form(
    request: Request,
    animal_id: int,
    session: Session = Depends(get_session),
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    animal = session.get(Animal, animal_id)
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    return templates.TemplateResponse(
        "animal_edit.html",
        {"request": request, "animal": animal, "user": user},
    )


@app.post("/animals/{animal_id}/edit", response_class=HTMLResponse)
def animal_edit(
    request: Request,
    animal_id: int,
    tag_no: str = Form(...),
    sex: str = Form(...),
    birthdate: date = Form(...),
    breed_name: Optional[str] = Form(None),
    cattle_type: str = Form("Cow"),
    mother_tag_no: Optional[str] = Form(None),
    lactating: Optional[str] = Form(None),
    pregnant: Optional[str] = Form(None),
    vaccinated: Optional[str] = Form(None),
    health: Optional[str] = Form(None),
    weight: float = Form(0.0),
    reproductions: int = Form(0),
    session: Session = Depends(get_session),
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    animal = session.get(Animal, animal_id)
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    breed_id = None
    if breed_name:
        breed = session.exec(select(Breed).where(Breed.name == breed_name)).first()
        if not breed:
            breed = Breed(name=breed_name)
            session.add(breed)
            session.commit()
            session.refresh(breed)
        breed_id = breed.id

    animal.tag_no = tag_no
    animal.sex = sex
    animal.birthdate = birthdate
    animal.breed_id = breed_id
    animal.cattle_type = cattle_type or "Cow"
    animal.mother_tag_no = mother_tag_no or None
    animal.lactating = form_bool(lactating)
    animal.pregnant = form_bool(pregnant)
    animal.vaccinated = form_bool(vaccinated)
    animal.health = health or None
    animal.weight = weight or 0.0
    animal.reproductions = int(reproductions or 0)

    try:
        session.add(animal)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=400, detail="Tag already exists")

    return RedirectResponse(url=f"/animals/{animal_id}", status_code=302)


@app.post("/animals/{animal_id}/delete")
def animal_delete(
    request: Request,
    animal_id: int,
    session: Session = Depends(get_session),
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    animal = session.get(Animal, animal_id)
    if not animal:
        raise HTTPException(status_code=404, detail="Not found")

    # remove related rows (simple approach)
    session.exec(select(MilkYieldDaily).where(MilkYieldDaily.animal_id == animal_id)).all()
    session.exec(select(BreedingEvent).where(BreedingEvent.animal_id == animal_id)).all()
    session.exec(select(Gestation).where(Gestation.animal_id == animal_id)).all()
    session.exec(select(VaccinationReminder).where(VaccinationReminder.animal_id == animal_id)).all()

    session.delete(animal)
    session.commit()
    return RedirectResponse(url="/animals", status_code=302)


@app.post("/milk/{animal_id}")
def add_milk(
    request: Request,
    animal_id: int,
    entry_date: date = Form(...),
    am_liters: float = Form(0.0),
    pm_liters: float = Form(0.0),
    session: Session = Depends(get_session),
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    total = am_liters + pm_liters
    existing = session.exec(
        select(MilkYieldDaily)
        .where(
            (MilkYieldDaily.animal_id == animal_id)
            & (MilkYieldDaily.entry_date == entry_date)
        )
    ).first()
    if existing:
        existing.am_liters = am_liters
        existing.pm_liters = pm_liters
        existing.total_liters = total
    else:
        session.add(
            MilkYieldDaily(
                animal_id=animal_id,
                entry_date=entry_date,
                am_liters=am_liters,
                pm_liters=pm_liters,
                total_liters=total,
            )
        )
    session.commit()
    return RedirectResponse(url=f"/animals/{animal_id}", status_code=302)


@app.post("/breeding/{animal_id}/event")
def add_breeding_event(
    request: Request,
    animal_id: int,
    event_type: BreedingType = Form(...),
    event_date: date = Form(...),
    notes: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    animal = session.get(Animal, animal_id)
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    event = BreedingEvent(
        animal_id=animal_id,
        event_type=event_type,
        event_date=event_date,
        notes=notes,
    )
    session.add(event)

    if event_type in {BreedingType.AI, BreedingType.NaturalService}:
        predicted = event_date + timedelta(days=DEFAULT_GESTATION_DAYS)
        gest = session.exec(
            select(Gestation)
            .where(Gestation.animal_id == animal_id)
            .order_by(Gestation.service_date.desc())
        ).first()
        if gest:
            gest.service_date = event_date
            gest.predicted_calving_date = predicted
        else:
            session.add(
                Gestation(
                    animal_id=animal_id,
                    service_date=event_date,
                    predicted_calving_date=predicted,
                )
            )

    session.commit()
    return RedirectResponse(url=f"/animals/{animal_id}", status_code=302)


@app.post("/animals/{animal_id}/vaccination_reminder")
def add_vaccination_reminder(
    request: Request,
    animal_id: int,
    reminder_date: date = Form(...),
    notes: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    animal = session.get(Animal, animal_id)
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    rem = VaccinationReminder(
        animal_id=animal_id,
        reminder_date=reminder_date,
        notes=notes,
    )
    session.add(rem)
    session.commit()

    return RedirectResponse(url=f"/animals/{animal_id}", status_code=302)


@app.post("/gestation/{animal_id}/calved")
def mark_calved(
    request: Request,
    animal_id: int,
    calving_date: date = Form(...),
    session: Session = Depends(get_session),
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    gest = session.exec(
        select(Gestation)
        .where(Gestation.animal_id == animal_id)
        .order_by(Gestation.service_date.desc())
    ).first()
    if not gest:
        raise HTTPException(status_code=404, detail="No gestation record")

    gest.actual_calving_date = calving_date
    session.commit()
    return RedirectResponse(url=f"/animals/{animal_id}", status_code=302)


# ---------------------------------------------------------------------
# Dashboard (preserved behavior)
# ---------------------------------------------------------------------


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    session: Session = Depends(get_session),
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    stmt_animals = select(Animal)
    if user.farm_id and not user.is_admin:
        stmt_animals = stmt_animals.where(Animal.farm_id == user.farm_id)
    animals = session.exec(stmt_animals).all()
    total_animals = len(animals)

    today = date.today()
    stmt_milk = select(MilkYieldDaily).where(MilkYieldDaily.entry_date == today)

    if user.farm_id and not user.is_admin:
        milk_rows = []
        for row in session.exec(stmt_milk).all():
            a = session.get(Animal, row.animal_id)
            if a and a.farm_id == user.farm_id:
                milk_rows.append(row)
    else:
        milk_rows = session.exec(stmt_milk).all()

    milk_today = sum(m.total_liters for m in milk_rows) if milk_rows else 0

    last7_start = today - timedelta(days=6)
    stmt7 = select(MilkYieldDaily).where(
        (MilkYieldDaily.entry_date >= last7_start)
        & (MilkYieldDaily.entry_date <= today)
    )
    milk7 = session.exec(stmt7).all()
    if milk7:
        total7 = sum(m.total_liters for m in milk7)
        avg_7 = round(total7 / 7, 2)
    else:
        avg_7 = 0

    gest_q = select(Gestation).where(
        (Gestation.predicted_calving_date >= today)
        & (Gestation.actual_calving_date.is_(None))
    )
    gest_list = session.exec(gest_q).all()
    if user.farm_id and not user.is_admin:
        pregnant_count = 0
        for g in gest_list:
            a = session.get(Animal, g.animal_id)
            if a and a.farm_id == user.farm_id:
                pregnant_count += 1
    else:
        pregnant_count = len(gest_list)

    start_chart = today - timedelta(days=13)
    chart_labels: List[str] = []
    chart_values: List[float] = []

    for offset in range(14):
        d = start_chart + timedelta(days=offset)
        label = d.strftime("%b %d")
        stmt_day = select(MilkYieldDaily).where(MilkYieldDaily.entry_date == d)
        day_rows = session.exec(stmt_day).all()
        if user.farm_id and not user.is_admin:
            day_total = 0.0
            for m in day_rows:
                a = session.get(Animal, m.animal_id)
                if a and a.farm_id == user.farm_id:
                    day_total += m.total_liters
        else:
            day_total = sum(m.total_liters for m in day_rows)
        chart_labels.append(label)
        chart_values.append(day_total)

    recent_events = session.exec(
        select(BreedingEvent).order_by(BreedingEvent.event_date.desc())
    ).all()[:5]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "total_animals": total_animals,
            "today_milk": milk_today,
            "avg_7": avg_7,
            "pregnant": pregnant_count,
            "chart_labels": chart_labels,
            "chart_values": chart_values,
            "recent_events": recent_events,
        },
    )
