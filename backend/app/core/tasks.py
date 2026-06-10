import logging

from celery import shared_task

from .models import TelegramUser
from .telegram_notifications import TelegramNotifier, format_technical_message

logger = logging.getLogger(__name__)


@shared_task(name="app.core.tasks.send_telegram_tech_message_task")
def send_telegram_tech_message_task(payload: dict) -> dict:
    payload = payload if isinstance(payload, dict) else {"details": {"payload": payload}}
    request_id = payload.get("request_id") or "-"
    telegram_id = payload.get("telegram_id")
    if not telegram_id:
        logger.info(
            "request_id=%s stage=telegram_notify event=skipped reason=no_source_user",
            request_id,
        )
        return {"status": "skipped", "reason": "no_source_user", "sent": 0}

    if not TelegramUser.objects.filter(telegram_id=telegram_id, is_superuser=True).exists():
        logger.info(
            "request_id=%s stage=telegram_notify event=skipped reason=source_user_not_superuser telegram_id=%s",
            request_id,
            telegram_id,
        )
        return {"status": "skipped", "reason": "source_user_not_superuser", "sent": 0}

    recipients = [telegram_id]

    notifier = TelegramNotifier()
    if not notifier.enabled():
        logger.info(
            "request_id=%s stage=telegram_notify event=skipped reason=no_token recipients=%s",
            request_id,
            len(recipients),
        )
        return {"status": "skipped", "reason": "no_token", "sent": 0}

    text = format_technical_message(payload)
    sent = 0
    errors: list[str] = []
    for chat_id in recipients:
        try:
            sent += notifier.send_message(chat_id, text)
        except Exception as exc:
            errors.append(f"{chat_id}: {exc}")
            logger.exception(
                "request_id=%s stage=telegram_notify event=send_failed chat_id=%s error=%s",
                request_id,
                chat_id,
                exc,
            )
    return {"status": "ok" if not errors else "partial", "recipients": len(recipients), "sent": sent, "errors": errors}
