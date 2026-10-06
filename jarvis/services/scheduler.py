from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from jarvis.config import settings
from jarvis.services import approvals, event_log, learning, macos, memory, notion_sync, screen_observer, tasks

scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))


async def morning_briefing() -> None:
    pending = await approvals.list_approvals(status="pending")
    recent = await tasks.list_tasks(limit=5)
    msg = (
        f"Good morning, boss. {len(pending)} items await your approval. "
        f"{len(recent)} recent tasks in the queue."
    )
    await macos.notify(settings.agent_name, msg, speak=True)
    await tasks.create_task(title="Morning briefing", body=msg, source="scheduler")


async def learning_report_refresh() -> None:
    await learning.periodic_refresh()


async def memory_compression() -> None:
    result = await memory.compress_stale_sessions()
    if result.get("compressed", 0) > 0:
        await tasks.create_task(
            title="Memory compressed",
            body=f"{result['compressed']} conversations summarized",
            source="memory",
        )


async def notion_export() -> None:
    if not notion_sync.configured():
        return
    events = await event_log.list_events(limit=30)
    if len(events) < 5:
        return
    result = await notion_sync.export_recent_events(events)
    if result.get("ok"):
        await tasks.create_task(
            title="Notion event export",
            body=f"Exported {len(events)} events",
            source="notion",
        )


async def screen_observer_tick() -> None:
    if not settings.screen_watch_enabled:
        return
    await screen_observer.observer_tick()


async def github_state_sync() -> None:
    """Push William state to william-hub on GitHub."""
    from jarvis.services import github_sync, state_export

    if not github_sync.configured():
        return
    result = await state_export.export_all()
    if result.get("ok"):
        await tasks.create_task(
            title="GitHub state sync",
            body=f"Exported to {result.get('hub_url', 'william-hub')}",
            source="github",
        )


async def voice_watchdog() -> None:
    """Keep William's voice pipeline alive 24/7."""
    from jarvis.services import ollama

    core = await ollama.health()
    if not core.get("ok"):
        await macos.restart_core_service()

    await macos.ensure_voice_awake()


async def popup_watchdog() -> None:
    """Dismiss permission dialogs using native Accessibility + vision fallback."""
    from jarvis.services import popup_handler, security

    if not settings.popup_handler_enabled:
        return
    if not await security.is_full_access():
        return
    await popup_handler.handle_popups(full_control=True, max_attempts=2)


async def provider_key_scan() -> None:
    """Key Scout: find new free-provider keys wherever they land (env, keys.env, Keychain)."""
    from jarvis.services.providers import scout

    await scout.scan_keys()


async def provider_probe() -> None:
    """Key Scout: cheap validity probes so dead keys are skipped before they cost a retry."""
    from jarvis.services.providers import scout

    await scout.probe_all()


async def provider_usage_rollup() -> None:
    """Quota Keeper: prune old usage rows and flag providers near free limits."""
    from jarvis.services.providers import scout

    await scout.usage_rollup()


def start() -> None:
    if scheduler.running:
        return
    scheduler.add_job(
        provider_key_scan,
        "interval",
        minutes=max(5, settings.provider_key_scan_interval_minutes),
        id="provider_key_scan",
        replace_existing=True,
    )
    scheduler.add_job(
        provider_probe,
        "interval",
        hours=max(1, settings.provider_probe_interval_hours),
        id="provider_probe",
        replace_existing=True,
    )
    scheduler.add_job(
        provider_usage_rollup,
        "cron",
        hour=0,
        minute=20,
        id="provider_usage_rollup",
        replace_existing=True,
    )
    scheduler.add_job(
        morning_briefing,
        "cron",
        hour=settings.briefing_hour,
        minute=0,
        id="morning_briefing",
        replace_existing=True,
    )
    scheduler.add_job(
        learning_report_refresh,
        "interval",
        hours=settings.learning_report_interval_hours,
        id="learning_report",
        replace_existing=True,
    )
    scheduler.add_job(
        memory_compression,
        "interval",
        hours=settings.memory_compress_interval_hours,
        id="memory_compression",
        replace_existing=True,
    )
    scheduler.add_job(
        voice_watchdog,
        "interval",
        minutes=settings.voice_watchdog_interval_minutes,
        id="voice_watchdog",
        replace_existing=True,
    )
    scheduler.add_job(
        notion_export,
        "interval",
        hours=settings.notion_export_interval_hours,
        id="notion_export",
        replace_existing=True,
    )
    scheduler.add_job(
        screen_observer_tick,
        "interval",
        seconds=settings.screen_observer_interval_seconds,
        id="screen_observer",
        replace_existing=True,
    )
    scheduler.add_job(
        popup_watchdog,
        "interval",
        seconds=settings.popup_watchdog_interval_seconds,
        id="popup_watchdog",
        replace_existing=True,
    )
    if getattr(settings, "github_sync_interval_hours", 0) > 0:
        scheduler.add_job(
            github_state_sync,
            "interval",
            hours=settings.github_sync_interval_hours,
            id="github_state_sync",
            replace_existing=True,
        )
    scheduler.start()


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def status() -> dict:
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })
    return {
        "running": scheduler.running,
        "timezone": settings.timezone,
        "jobs": jobs,
    }
