"""План предпринимателя → события прогноза: ручные события («клиент обещал 300 000 ₽ 20-го»)."""
from __future__ import annotations

from app.engine.types import PlannedEvent
from app.schemas import EventCreate


def manual_event(event: EventCreate, event_id: str | int) -> PlannedEvent:
    """Ручное событие из формы «Добавить событие». Суммы заданы точно, неопределённость — в `probability`."""
    return PlannedEvent(
        id=f"manual:{event_id}", direction=1 if event.direction == "in" else -1, amount=event.amount, date=event.date,
        p_within_horizon=event.probability, amount_cv=0.0, category="manual", title=event.title, source="manual",
        mandatory=event.mandatory, counterparty=event.counterparty_name,
        explain="Добавлено вручную" + (f", уверенность {round(event.probability * 100)}%" if event.probability < 1 else ""),
    )
