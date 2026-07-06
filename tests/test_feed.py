"""
tests/test_feed.py — Mixtape

Tests for Friends Listening Now feed logic.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed_feed(app):
    with app.app_context():
        viewer = User(username="viewer", email="viewer@example.com")
        friend_today = User(username="today_friend", email="today@example.com")
        friend_yesterday = User(username="yesterday_friend", email="yesterday@example.com")
        db.session.add_all([viewer, friend_today, friend_yesterday])
        db.session.flush()

        for friend in (friend_today, friend_yesterday):
            db.session.execute(
                friendships.insert().values(user_id=viewer.id, friend_id=friend.id)
            )
            db.session.execute(
                friendships.insert().values(user_id=friend.id, friend_id=viewer.id)
            )

        song = Song(title="Test Track", artist="Test Artist", shared_by=friend_today.id)
        db.session.add(song)
        db.session.flush()

        now = datetime.now(timezone.utc)
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        db.session.add(
            ListeningEvent(
                user_id=friend_yesterday.id,
                song_id=song.id,
                listened_at=start_of_today - timedelta(hours=1),
            )
        )
        db.session.add(
            ListeningEvent(
                user_id=friend_today.id,
                song_id=song.id,
                listened_at=start_of_today + timedelta(hours=1),
            )
        )
        db.session.commit()

        yield {"viewer": viewer}


def test_listening_now_excludes_yesterday(app, seed_feed):
    """Friends who only listened yesterday should not appear in Friends Listening Now."""
    with app.app_context():
        feed = get_friends_listening_now(seed_feed["viewer"].id)
        usernames = [entry["friend"]["username"] for entry in feed]
        assert "today_friend" in usernames
        assert "yesterday_friend" not in usernames
