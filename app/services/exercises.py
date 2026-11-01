"""Библиотека упражнений: каталог, посев в БД, прогресс и streak.

Упражнения разбиты по категориям, совпадающим с категориями метрик
(jawline, posture, skin, eyes, symmetry). Каждое упражнение привязано к
конкретным метрикам, которые оно потенциально улучшает, — это позволяет
подбирать рекомендации «под зону роста» из отчёта.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exercise import Exercise, ExerciseCompletion


@dataclass(frozen=True, slots=True)
class ExerciseSpec:
    """Описание упражнения в каталоге."""

    slug: str
    title: str
    category: str
    description: str
    instructions: str
    duration_seconds: int
    difficulty: str
    targets: tuple[str, ...]


EXERCISE_CATALOG: tuple[ExerciseSpec, ...] = (
    ExerciseSpec(
        slug="mewing-basics",
        title="Мьюинг: базовое положение языка",
        category="jawline",
        description="Положение языка у нёба поддерживает подъязычную линию и тонус челюсти.",
        instructions=(
            "1. Расслабьте челюсть, губы сомкнуты без напряжения.\n"
            "2. Поднимите весь язык (не только кончик) к нёбу.\n"
            "3. Дышите носом, держите положение 5 минут.\n"
            "4. Повторите 3 раза в день."
        ),
        duration_seconds=300,
        difficulty="easy",
        targets=("jaw_width_ratio", "gonial_angle", "chin_projection"),
    ),
    ExerciseSpec(
        slug="jaw-resistance",
        title="Сопротивление для жевательных мышц",
        category="jawline",
        description="Мягкая нагрузка на жевательные мышцы усиливает контур нижней челюсти.",
        instructions=(
            "1. Жуйте жёсткую пищу (морковь, яблоко) 10 минут.\n"
            "2. Следите за симметрией: жуйте на обе стороны по 30 секунд.\n"
            "3. Не переусердствуйте — при боли в суставе прекратите."
        ),
        duration_seconds=600,
        difficulty="medium",
        targets=("gonial_angle", "jaw_width_ratio"),
    ),
    ExerciseSpec(
        slug="neck-posture",
        title="Осанка шеи и подбородка",
        category="posture",
        description="Прямая шея убирает «текстовую шею» и визуально усиливает линию челюсти.",
        instructions=(
            "1. Встаньте у стены, затылок и лопатки касаются стены.\n"
            "2. Слегка втяните подбородок (chin tuck), не наклоняя голову.\n"
            "3. Держите 10 секунд, 10 повторов.\n"
            "4. Проверяйте положение каждые 30 минут за компьютером."
        ),
        duration_seconds=180,
        difficulty="easy",
        targets=("jaw_width_ratio", "chin_projection", "lower_face_ratio"),
    ),
    ExerciseSpec(
        slug="facial-symmetry-checks",
        title="Симметрия мимики",
        category="symmetry",
        description="Осознанная симметричная мимика снижает привычную асимметрию.",
        instructions=(
            "1. Перед зеркалом улыбнитесь, удерживая углы губ на одном уровне.\n"
            "2. Поднимите брови симметрично 10 раз.\n"
            "3. Сожмите губы в трубочку и расслабьте 15 раз.\n"
            "4. Следите за стороной, которая «отстаёт»."
        ),
        duration_seconds=240,
        difficulty="easy",
        targets=("facial_symmetry",),
    ),
    ExerciseSpec(
        slug="eye-relaxation",
        title="Гимнастика для глаз и периорбитальной зоны",
        category="eyes",
        description="Снимает напряжение век и улучшает тонус мышц вокруг глаз.",
        instructions=(
            "1. Медленно переводите взгляд вверх-вниз, влево-вправо (10 раз).\n"
            "2. Плотно зажмурьтесь на 3 секунды, расслабьте (15 повторов).\n"
            "3. Правило 20-20-20: каждые 20 минут смотрите 20 секунд на 20 футов."
        ),
        duration_seconds=180,
        difficulty="easy",
        targets=("canthal_tilt", "eye_width_ratio", "eye_spacing"),
    ),
    ExerciseSpec(
        slug="sleep-and-fluids",
        title="Сон и водный баланс",
        category="skin",
        description="Отёчность под глазами усиливает негативный наклон внешнего угла.",
        instructions=(
            "1. Спите 7–9 часов в одно и то же время.\n"
            "2. Не ешьте солёное за 3 часа до сна.\n"
            "3. 30–35 мл воды на кг веса в сутки."
        ),
        duration_seconds=60,
        difficulty="easy",
        targets=("canthal_tilt",),
    ),
    ExerciseSpec(
        slug="sun-and-skincare",
        title="Базовый уход и фотозащита",
        category="skin",
        description="Ровный тон кожи повышает воспринимаемое качество лица.",
        instructions=(
            "1. Утром: мягкое очищение, увлажнение, SPF 30+.\n"
            "2. Вечером: очищение и увлажнение.\n"
            "3. Не выдавливайте воспаления — риск рубцов."
        ),
        duration_seconds=120,
        difficulty="easy",
        targets=("facial_symmetry",),
    ),
    ExerciseSpec(
        slug="chewing-symmetry",
        title="Симметричное жевание",
        category="symmetry",
        description="Привычка жевать на одну сторону смещает центр лица.",
        instructions=(
            "1. Отслеживайте, на какой стороне жуёте чаще.\n"
            "2. Первые 10 жевательных движений делайте на «слабую» сторону.\n"
            "3. Держите баланс в течение дня."
        ),
        duration_seconds=60,
        difficulty="easy",
        targets=("facial_symmetry", "facial_fifths"),
    ),
    ExerciseSpec(
        slug="posture-shoulders",
        title="Раскрытие плеч",
        category="posture",
        description="Сутулость визуально уменьшает лицо и добавляет второй подбородок.",
        instructions=(
            "1. Сведите лопатки, задержите 5 секунд (15 повторов).\n"
            "2. Растяжка грудных мышц в дверном проёме 30 секунд × 3.\n"
            "3. Проверяйте осанку по таймеру каждый час."
        ),
        duration_seconds=300,
        difficulty="medium",
        targets=("lower_face_ratio", "facial_thirds"),
    ),
    ExerciseSpec(
        slug="lymphatic-massage",
        title="Лимфодренажный самомассаж",
        category="skin",
        description="Мягкий массаж уменьшает утреннюю отёчность лица.",
        instructions=(
            "1. Чистыми руками — от центра лица к ушам и вниз к ключицам.\n"
            "2. Лёгкое давление, 5 минут.\n"
            "3. Не растягивайте кожу, используйте увлажняющее средство."
        ),
        duration_seconds=300,
        difficulty="easy",
        targets=("facial_fifths", "facial_symmetry"),
    ),
)


async def seed_exercises(db: AsyncSession) -> int:
    """Создаёт недостающие упражнения из каталога. Возвращает число добавленных."""
    existing = set((await db.execute(select(Exercise.slug))).scalars().all())
    added = 0
    for spec in EXERCISE_CATALOG:
        if spec.slug in existing:
            continue
        db.add(
            Exercise(
                title=spec.title,
                slug=spec.slug,
                category=spec.category,
                description=spec.description,
                instructions=spec.instructions,
                duration_seconds=spec.duration_seconds,
                difficulty=spec.difficulty,
                is_active=True,
            )
        )
        added += 1
    if added:
        await db.commit()
    return added


async def list_exercises(db: AsyncSession, category: str | None = None) -> list[Exercise]:
    """Список активных упражнений (опционально по категории)."""
    stmt = select(Exercise).where(Exercise.is_active.is_(True))
    if category:
        stmt = stmt.where(Exercise.category == category)
    return list(
        (await db.execute(stmt.order_by(Exercise.category, Exercise.title))).scalars().all()
    )


async def get_exercise(db: AsyncSession, slug: str) -> Exercise | None:
    """Упражнение по slug."""
    return (await db.execute(select(Exercise).where(Exercise.slug == slug))).scalar_one_or_none()


async def complete_exercise(
    db: AsyncSession, user_id: int, exercise: Exercise, note: str | None = None
) -> tuple[ExerciseCompletion, bool]:
    """Отмечает выполнение упражнения за сегодня.

    Возвращает (запись, создана_ли_новая). Повторный вызов в тот же день не
    дублирует запись (ограничение уникальным индексом uq_completion_day).
    """
    today = datetime.now(UTC).date()
    existing = (
        await db.execute(
            select(ExerciseCompletion).where(
                ExerciseCompletion.user_id == user_id,
                ExerciseCompletion.exercise_id == exercise.id,
                ExerciseCompletion.completed_on == today,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        if note and existing.note != note:
            existing.note = note
            await db.commit()
        return existing, False

    completion = ExerciseCompletion(
        user_id=user_id,
        exercise_id=exercise.id,
        completed_on=today,
        note=note,
    )
    db.add(completion)
    await db.commit()
    await db.refresh(completion)
    return completion, True


async def completion_days(db: AsyncSession, user_id: int, days: int = 30) -> set[date]:
    """Множество дней с хотя бы одним выполненным упражнением."""
    since = datetime.now(UTC).date() - timedelta(days=days - 1)
    rows = (
        (
            await db.execute(
                select(ExerciseCompletion.completed_on)
                .where(
                    ExerciseCompletion.user_id == user_id,
                    ExerciseCompletion.completed_on >= since,
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


async def exercise_streak(db: AsyncSession, user_id: int) -> int:
    """Серия дней подряд с выполненными упражнениями (считая от сегодня/вчера)."""
    days = await completion_days(db, user_id, days=365)
    if not days:
        return 0

    today = datetime.now(UTC).date()
    start = today if today in days else today - timedelta(days=1)
    if start not in days:
        return 0

    streak = 0
    cursor = start
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def total_completions(db: AsyncSession, user_id: int) -> int:
    """Общее число выполненных упражнений (включая повторы по дням)."""
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(ExerciseCompletion)
                .where(ExerciseCompletion.user_id == user_id)
            )
        ).scalar_one()
    )


async def category_progress(db: AsyncSession, user_id: int) -> dict[str, int]:
    """Сколько раз пользователь выполнял упражнения по категориям."""
    rows = (
        await db.execute(
            select(Exercise.category, func.count())
            .join(ExerciseCompletion, ExerciseCompletion.exercise_id == Exercise.id)
            .where(ExerciseCompletion.user_id == user_id)
            .group_by(Exercise.category)
        )
    ).all()
    return {category: int(count) for category, count in rows}


def recommend_by_targets(
    exercises: list[Exercise], growth_keys: set[str], limit: int = 4
) -> list[Exercise]:
    """Подбирает упражнения под зоны роста отчёта.

    Упражнение попадает в подборку, если хотя бы одна из его целевых метрик
    присутствует в зоне роста. Если совпадений нет — возвращаем первые по порядку.
    """
    scored: list[tuple[int, Exercise]] = []
    for exercise in exercises:
        spec = next((s for s in EXERCISE_CATALOG if s.slug == exercise.slug), None)
        if spec is None:
            continue
        overlap = len(set(spec.targets) & growth_keys)
        if overlap:
            scored.append((overlap, exercise))

    scored.sort(key=lambda item: (-item[0], item[1].title))
    result = [exercise for _, exercise in scored[:limit]]
    if result:
        return result
    return exercises[:limit]
