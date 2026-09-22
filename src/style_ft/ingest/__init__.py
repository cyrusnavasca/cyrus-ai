"""Readers that turn a message source into the common JSONL message record.

Every reader emits the same shape, so nothing downstream knows or cares whether
the messages came from the macOS database or a third-party export:

    {"sender", "text", "timestamp", "thread_id", "direction", ...}
"""
