"""Pure view models for dashboard state; safe to test without Tk or Windows."""

from __future__ import annotations

from dataclasses import dataclass

try:
    from .core import ComponentState
    from .gemini_availability import GeminiNetworkStatus
except ImportError:
    from core import ComponentState
    from gemini_availability import GeminiNetworkStatus


DASHBOARD_PAGES = ("vpn", "google", "ag", "settings")


@dataclass(frozen=True)
class ModePresentation:
    label: str
    action: str
    tone: str


def vpn_presentation(state: ComponentState, error: str | None = None) -> ModePresentation:
    if state is ComponentState.RUNNING:
        return ModePresentation("Подключено", "Отключить", "success")
    if state is ComponentState.DEGRADED:
        return ModePresentation(error or "Соединение работает с ограничениями", "Отключить", "warning")
    if state in (ComponentState.STARTING, ComponentState.STOPPING, ComponentState.RECOVERING):
        return ModePresentation("Подключение…" if state is not ComponentState.STOPPING else "Отключение…",
                                "Подождите…", "busy")
    if state is ComponentState.ERROR:
        return ModePresentation(error or "Ошибка подключения", "Повторить", "error")
    return ModePresentation("Выключено", "Подключить", "muted")


def gemini_access_summary(status: GeminiNetworkStatus) -> tuple[str, str]:
    """Return separate site and account messages without inferring account access."""
    if status.website_reachable is True:
        website = "Сайт Google отвечает"
    elif status.website_reachable is False:
        website = "Сайт недоступен по текущему маршруту"
    else:
        website = "Сайт ещё не проверен"
    return website, "Google Account: ? не проверен"
