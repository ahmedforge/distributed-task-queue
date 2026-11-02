"""Compatibility import; all job access uses the authenticated router."""

from app.api.v1.jobs import job_progress_websocket, router

__all__ = ["job_progress_websocket", "router"]
