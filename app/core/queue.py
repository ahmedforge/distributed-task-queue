from saq import Queue
from app.core.config import settings

queue = Queue.from_url(settings.REDIS_URL, name="tasks")