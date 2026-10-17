import os

os.environ["PLAYBOOK_ENABLED"] = "false"

import pytest
from sqlalchemy.orm import Session

from wellwell.db.session import get_engine


@pytest.fixture
def session():
    """A session on the real database whose changes are always rolled back."""
    connection = get_engine().connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


class ScriptedLLM:
    """Replays canned model replies so the harness can be tested without the network."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, system, prompt):
        from wellwell.harness.llm import Completion, LLMError

        self.prompts.append(prompt)
        if not self.replies:
            raise LLMError("no more scripted replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Completion(reply, "scripted", 1)
