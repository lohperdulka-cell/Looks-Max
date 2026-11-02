"""Пакет ORM-моделей. Импорт всех моделей регистрирует их в Base.metadata."""

from app.models.battle import Battle, BattleSeason, Vote
from app.models.chat import ChatMessage, ChatSession, LooksmaxPlan
from app.models.content import (
    AdminLog,
    Article,
    ErrorLog,
    FaqItem,
    Feedback,
    MetricWeight,
    PageView,
)
from app.models.exercise import (
    Achievement,
    Exercise,
    ExerciseCompletion,
    ProgressSnapshot,
    UserAchievement,
)
from app.models.mogbattle import MogRoom
from app.models.photo import Photo
from app.models.report import MetricValue, Report
from app.models.user import PasswordResetToken, RefreshToken, User

__all__ = [
    "Achievement",
    "AdminLog",
    "Article",
    "Battle",
    "BattleSeason",
    "ChatMessage",
    "ChatSession",
    "ErrorLog",
    "Exercise",
    "ExerciseCompletion",
    "FaqItem",
    "Feedback",
    "LooksmaxPlan",
    "MetricValue",
    "MetricWeight",
    "MogRoom",
    "PageView",
    "PasswordResetToken",
    "Photo",
    "ProgressSnapshot",
    "RefreshToken",
    "Report",
    "User",
    "UserAchievement",
    "Vote",
]
