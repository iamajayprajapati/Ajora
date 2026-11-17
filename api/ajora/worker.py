import os

from celery import Celery

from .cli import crawl

worker = Celery("ajora", broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
worker.conf.update(
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    broker_connection_timeout=3,
    beat_schedule={"monitor-approved-sources": {"task": "ajora.monitor", "schedule": 15 * 60}},
)


@worker.task(name="ajora.monitor")
def monitor(full=False):
    crawl(full=full)


def enqueue_daily_refresh():
    return worker.send_task("ajora.monitor", kwargs={"full": True}, retry=False).id
