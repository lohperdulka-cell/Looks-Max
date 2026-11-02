"""Роутер профиля: статистика, прогресс, до/после, достижения, обновление."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, DbSession
from app.models.report import MetricValue
from app.models.user import User
from app.schemas.profile import (
    AchievementOut,
    ActivityDayOut,
    BeforeAfterOut,
    CompareMetric,
    GamificationOut,
    LevelOut,
    ProfileOut,
    ProfileStatsOut,
    ProfileUpdateRequest,
    ProgressOut,
    ProgressPointOut,
    QuestOut,
    XpSourceOut,
)
from app.services import achievements as achievements_service
from app.services import gamification as gamification_service
from app.services import profile as profile_service

router = APIRouter(prefix="/api/profile", tags=["profile"])


async def _build_stats(db: AsyncSession, user: User) -> ProfileStatsOut:
    stats = await profile_service.collect_stats(db, user)
    wins, losses = await profile_service.battle_record(db, user.id)
    return ProfileStatsOut(
        photos_count=stats.photos_count,
        reports_count=stats.reports_count,
        exercises_count=stats.exercises_count,
        battle_votes=stats.battle_votes,
        best_score=stats.best_score,
        latest_score=stats.latest_score,
        average_score=stats.average_score,
        improvement=stats.improvement,
        streak_days=stats.streak_days,
        elo=stats.elo,
        public_reports=stats.public_reports,
        wins=wins,
        losses=losses,
    )


@router.get("", response_model=ProfileOut)
async def get_profile(user: CurrentUser, db: DbSession) -> ProfileOut:
    """Профиль текущего пользователя со статистикой."""
    return ProfileOut(
        username=user.username,
        display_name=user.display_name,
        gender=user.gender,
        birth_year=user.birth_year,
        created_at=user.created_at,
        elo=user.elo,
        streak_days=user.streak_days,
        show_in_battles=user.show_in_battles,
        stats=await _build_stats(db, user),
    )


@router.patch("", response_model=ProfileOut)
async def update_profile(
    payload: ProfileUpdateRequest, user: CurrentUser, db: DbSession
) -> ProfileOut:
    """Обновляет профиль. Отметка активности обновляет streak."""
    changed = False
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip() or None
        changed = True
    if payload.gender is not None:
        user.gender = payload.gender
        changed = True
    if payload.birth_year is not None:
        user.birth_year = payload.birth_year
        changed = True
    if payload.show_in_battles is not None:
        user.show_in_battles = payload.show_in_battles
        changed = True

    if changed:
        await db.commit()
        await db.refresh(user)
        await profile_service.touch_activity(db, user)

    return ProfileOut(
        username=user.username,
        display_name=user.display_name,
        gender=user.gender,
        birth_year=user.birth_year,
        created_at=user.created_at,
        elo=user.elo,
        streak_days=user.streak_days,
        show_in_battles=user.show_in_battles,
        stats=await _build_stats(db, user),
    )


@router.get("/progress", response_model=ProgressOut)
async def get_progress(
    user: CurrentUser, db: DbSession, days: int = Query(default=30, ge=7, le=365)
) -> ProgressOut:
    """Ряд прогресса по дням для line-графика."""
    points = await profile_service.progress_series(db, user.id, days=days)
    scored = [p.overall_score for p in points if p.overall_score is not None]
    delta = round(scored[-1] - scored[0], 2) if len(scored) >= 2 else 0.0
    return ProgressOut(
        days=days,
        points=[
            ProgressPointOut(
                day=p.day, overall_score=p.overall_score, reports_count=p.reports_count
            )
            for p in points
        ],
        delta=delta,
    )


@router.get("/before-after", response_model=BeforeAfterOut)
async def get_before_after(user: CurrentUser, db: DbSession) -> BeforeAfterOut:
    """Сравнение первого и последнего отчёта по метрикам."""
    first, last = await profile_service.before_after(db, user.id)
    if first is None or last is None or first.id == last.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Нужно минимум два отчёта для сравнения",
        )

    before_rows = (
        (await db.execute(select(MetricValue).where(MetricValue.report_id == first.id)))
        .scalars()
        .all()
    )
    after_rows = (
        (await db.execute(select(MetricValue).where(MetricValue.report_id == last.id)))
        .scalars()
        .all()
    )

    after_map = {row.key: row for row in after_rows}
    metrics: list[CompareMetric] = []
    for row in before_rows:
        other = after_map.get(row.key)
        if other is None:
            continue
        metrics.append(
            CompareMetric(
                key=row.key,
                label=row.label,
                before=round(row.score, 2),
                after=round(other.score, 2),
                delta=round(other.score - row.score, 2),
            )
        )
    metrics.sort(key=lambda m: m.delta, reverse=True)

    return BeforeAfterOut(
        before_report_id=first.id,
        after_report_id=last.id,
        before_date=first.created_at,
        after_date=last.created_at,
        before_overall=first.overall_score,
        after_overall=last.overall_score,
        overall_delta=round(last.overall_score - first.overall_score, 2),
        metrics=metrics,
    )


@router.get("/achievements", response_model=list[AchievementOut])
async def list_achievements(user: CurrentUser, db: DbSession) -> list[AchievementOut]:
    """Все достижения: полученные и доступные, с прогрессом выполнения."""
    stats = await profile_service.collect_stats(db, user)
    achievement_stats = profile_service.stats_to_achievement_stats(stats)

    earned_rows = await achievements_service.list_user_achievements(db, user.id)
    earned = {row.code: awarded_at for row, awarded_at in earned_rows}

    out: list[AchievementOut] = []
    for spec in achievements_service.catalog_preview():
        awarded_at = earned.get(spec["code"])
        out.append(
            AchievementOut(
                code=spec["code"],
                title=spec["title"],
                description=spec["description"],
                icon=spec["icon"],
                threshold=spec["threshold"],
                awarded_at=awarded_at,
                earned=awarded_at is not None,
                progress=achievements_service.progress_ratio(spec["code"], achievement_stats),
            )
        )
    out.sort(key=lambda a: (not a.earned, a.code))
    return out


_XP_LABELS: dict[str, str] = {
    "photos": "Фото",
    "reports": "Отчёты",
    "exercises": "Упражнения",
    "votes": "Голоса на арене",
    "mog_wins": "Победы в моггбатлах",
    "mog_losses": "Участие в дуэлях",
    "mog_draws": "Ничьи в дуэлях",
    "streak_days": "Серия активности",
}


def _xp_sources(breakdown: gamification_service.XpBreakdown) -> list[XpSourceOut]:
    """Разложение XP по источникам, только непустые строки."""
    rates = {
        "photos": gamification_service.XP_PHOTO,
        "reports": gamification_service.XP_REPORT,
        "exercises": gamification_service.XP_EXERCISE,
        "votes": gamification_service.XP_VOTE,
        "mog_wins": gamification_service.XP_MOG_WIN,
        "mog_losses": gamification_service.XP_MOG_LOSS,
        "mog_draws": gamification_service.XP_MOG_DRAW,
        "streak_days": gamification_service.XP_STREAK_DAY,
    }
    out: list[XpSourceOut] = []
    for code, label in _XP_LABELS.items():
        count = int(getattr(breakdown, code, 0))
        if count <= 0:
            continue
        out.append(XpSourceOut(code=code, label=label, count=count, xp=count * rates[code]))
    out.sort(key=lambda s: s.xp, reverse=True)
    return out


@router.get("/gamification", response_model=GamificationOut)
async def get_gamification(user: CurrentUser, db: DbSession) -> GamificationOut:
    """Уровень, опыт, ежедневные задания и карта активности."""
    breakdown = await gamification_service.collect_breakdown(db, user)
    info = gamification_service.level_info(breakdown.total)
    counts = await gamification_service.today_counts(db, user)

    quests = [
        QuestOut(
            code=q.code,
            title=q.title,
            description=q.description,
            target=q.target,
            done=min(counts.get(q.metric, 0), q.target),
            completed=counts.get(q.metric, 0) >= q.target,
            xp_reward=q.xp_reward,
        )
        for q in gamification_service.DAILY_QUESTS
    ]

    series = await gamification_service.activity_series(db, user, days=30)
    activity = [
        ActivityDayOut(
            day=d.day,
            total=d.total,
            intensity=d.intensity,
            breakdown={
                "photos": d.photos,
                "reports": d.reports,
                "exercises": d.exercises,
                "votes": d.votes,
                "mog": d.mog,
            },
        )
        for d in series
    ]

    return GamificationOut(
        level=LevelOut(
            level=info.level,
            title=info.title,
            xp=info.xp,
            xp_into_level=info.xp_into_level,
            xp_for_next=info.xp_for_next,
            progress=info.progress,
        ),
        total_xp=breakdown.total,
        sources=_xp_sources(breakdown),
        quests=quests,
        quests_done=sum(1 for q in quests if q.completed),
        activity=activity,
        active_days=sum(1 for d in activity if d.total > 0),
    )


@router.post("/achievements/sync", response_model=list[AchievementOut])
async def sync_achievements(user: CurrentUser, db: DbSession) -> list[AchievementOut]:
    """Пересчитывает и выдаёт достижения по текущей статистике."""
    stats = await profile_service.collect_stats(db, user)
    await achievements_service.award_achievements(
        db, user, profile_service.stats_to_achievement_stats(stats)
    )
    return await list_achievements(user, db)
