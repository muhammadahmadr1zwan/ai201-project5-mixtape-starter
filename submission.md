# Mixtape Bug Hunt — Submission

## AI Usage

I used Cursor's AI assistant throughout this project as a debugging collaborator, not as a substitute for reading the code.

**Codebase orientation:** I asked the AI to summarize the repo structure from the README and trace call chains (e.g., rating a song from `routes/songs.py` through `notification_service.rate_song()`). That helped me build the codebase map quickly, but I verified every claim by opening the actual files.

**Bug investigation:** For each issue, I read the affected service file first, then used the AI to explain edge cases (e.g., Python's `weekday()` vs `isoweekday()`, why SQL joins can multiply rows). The AI correctly identified the Sunday streak guard (`today.weekday() != 6`) and the playlist slice (`songs[:-1]`) as root causes. For the search duplicate issue, I had to verify myself that the unnecessary `outerjoin` on `song_tags` multiplies rows when a song has multiple tags — the AI suggested `.distinct()`, but removing the unused join was the cleaner fix.

**Where I overrode the AI:** The feed bug fix could use either a rolling 24-hour window or a calendar-day cutoff. After reading `seed_data.py` (which labels events as "should NOT appear in listening now"), I chose start-of-day UTC rather than accepting the AI's first suggestion of a shorter rolling window.

**Regression tests:** Existing tests in `tests/` already cover streaks, search, and playlists. I added `tests/test_feed.py` for the feed fix.

---

## Codebase Map

Mixtape is a Flask + SQLAlchemy social music app. Routes handle HTTP parsing and JSON responses; all business logic lives in `services/`.

### Main Files

| File | Role |
|------|------|
| `app.py` | Flask app factory, SQLAlchemy `db` init, blueprint registration |
| `models.py` | ORM models: `User`, `Song`, `Tag`, `ListeningEvent`, `Rating`, `Playlist`, `Notification`; association tables for friendships, song tags, and playlist entries (with `position` ordering) |
| `routes/songs.py` | Search, song detail, rate, and listen endpoints |
| `routes/playlists.py` | Create playlist, get songs, add song to playlist |
| `routes/users.py` | User profile, streak, notifications |
| `routes/feed.py` | Friends listening now, activity feed |
| `services/*.py` | Business logic for streaks, feed, search, notifications, playlists |
| `seed_data.py` | Populates SQLite with users, friendships, songs, playlists, listening events |
| `tests/` | Pytest suites for streaks, search, and playlists |

### Pattern

Every route delegates immediately to a service function. Routes never touch the database directly except `routes/users.py` for simple user lookup.

### Data Flow — Friend Adds Your Song to a Playlist

1. `POST /playlists/<playlist_id>/songs` with `{song_id, added_by}` → `routes/playlists.py` `add_song()`
2. Calls `notification_service.add_to_playlist(playlist_id, song_id, added_by)`
3. Service loads the `Song`, `User` (adder), and `Playlist`
4. Appends song to `playlist.songs` relationship if not already present, commits
5. If `song.shared_by != added_by`, calls `create_notification()` with type `song_added_to_playlist`
6. `create_notification()` inserts a `Notification` row and commits

A related flow — rating a song — follows the same route-to-service pattern: `POST /songs/<id>/rate` → `routes/songs.py` → `notification_service.rate_song()`, which persists a `Rating` and may also notify the song's original sharer.

---

## Root Cause Analysis

### Issue #1 — My listening streak keeps resetting

**How you reproduced it:** Ran `pytest tests/test_streaks.py::test_streak_increments_on_sunday -v`. The test simulates listening on Saturday 2024-06-15 then Sunday 2024-06-16. Expected streak 2, got 1.

**How you found the root cause:** Read `services/streak_service.py` → `update_listening_streak()`. Traced the `days_since_last == 1` branch and noticed an extra guard `today.weekday() != 6`.

**The root cause:** When `days_since_last == 1` (listened yesterday), the code only increments the streak if today is not Sunday (`weekday() != 6`). On Sunday after listening Saturday, the condition fails and the streak resets to 1 instead of incrementing.

**Your fix and side-effect check:** Removed the `today.weekday() != 6` guard so consecutive calendar days always increment. Ran full `tests/test_streaks.py` — all 5 tests pass, including same-day no-double-count and skip-day reset.

---

### Issue #2 — Friends Listening Now shows people from yesterday

**How you reproduced it:** Read `seed_data.py` — events at `now - timedelta(hours=2 + i * 8)` for i=0..7 fall within a rolling 24-hour window even when they happened yesterday evening. Called `get_friends_listening_now()` for a user with friends and observed events older than today's calendar day in results.

**How you found the root cause:** `routes/feed.py` → `feed_service.get_friends_listening_now()`. Found `cutoff = now - timedelta(hours=24)` using a rolling window instead of a calendar-day boundary.

**The root cause:** A rolling 24-hour threshold includes listening events from late yesterday that are still within 24 hours of now. "Listening now" should mean today, not "anytime in the last 24 hours."

**Your fix and side-effect check:** Changed cutoff to start of today UTC: `now.replace(hour=0, minute=0, second=0, microsecond=0)`. Added `tests/test_feed.py` to verify yesterday's events are excluded. `get_activity_feed()` unchanged — it intentionally returns all recent friend activity without day filtering.

---

### Issue #3 — The same song keeps showing up twice in search

**How you reproduced it:** Inspected `search_service.search_songs()` — the query `outerjoin`s `song_tags` even though tags are not part of the filter. Songs with 3 tags produce 3 SQL rows. With SQLAlchemy 2.0 `scalars()`, this returns 3 copies of the same song. Existing `tests/test_search.py` documents this for multi-tag songs.

**How you found the root cause:** Read `services/search_service.py`. The outer join on `song_tags` is unnecessary — tags load via the `Song.tags` relationship in `to_dict()`. The join only multiplies rows for multi-tag songs (conditional duplicate).

**The root cause:** The `outerjoin(song_tags, ...)` creates one result row per tag. A song with N tags appears N times in search results.

**Your fix and side-effect check:** Removed the unused `outerjoin` and `song_tags` import. Search now queries `Song` directly. All `tests/test_search.py` tests pass. Songs with 0, 1, or 3+ tags each appear exactly once.

---

### Issue #4 — Notified when friend adds song to playlist but not when they rate it

**How you reproduced it:** Compared `add_to_playlist()` and `rate_song()` in `notification_service.py` line by line. `add_to_playlist` calls `create_notification()` when a friend adds your song; `rate_song` saves the rating but never notifies.

**How you found the root cause:** Traced `POST /songs/<id>/rate` → `routes/songs.py` → `rate_song()`. The working notification pattern in `add_to_playlist()` was not replicated in `rate_song()`.

**The root cause:** Architectural omission — `rate_song()` handles persistence only. No call to `create_notification()` after a successful rating, unlike `add_to_playlist()` which notifies `song.shared_by` when `added_by != shared_by`.

**Your fix and side-effect check:** After commit, if `song.shared_by != user_id`, call `create_notification()` with type `song_rated` and a message including rater username, song title, and score. Self-ratings still do not notify. Rating validation and update-or-create logic unchanged.

---

### Issue #5 — The last song in a playlist never shows up

**How you reproduced it:** Ran `pytest tests/test_playlists.py -v`. `test_playlist_returns_all_songs` expected 5 songs, got 4. Missing title: "Track 5".

**How you found the root cause:** `routes/playlists.py` → `playlist_service.get_playlist_songs()`. Query correctly fetches all songs ordered by position, but return line uses `songs[:-1]`.

**The root cause:** `return [song.to_dict() for song in songs[:-1]]` slices off the last element. Every playlist loses its final song.

**Your fix and side-effect check:** Changed to `songs` (no slice). All 3 playlist tests pass, including empty playlist and order verification.

---

## Regression Test Reference

- **Issue #1:** `tests/test_streaks.py::test_streak_increments_on_sunday` — simulates Saturday then Sunday listening; asserts streak reaches 2. Would fail against buggy code because the `today.weekday() != 6` guard resets the streak to 1 on Sunday.
- **Issue #2:** `tests/test_feed.py::test_listening_now_excludes_yesterday` (added) — seeds a friend who only listened yesterday and one who listened today; asserts only today's friend appears. Would fail against buggy code because the rolling 24-hour window still includes yesterday evening's events.
- **Issue #3:** `tests/test_search.py::test_search_no_duplicates_multi_tag_song` — searches for a 3-tag song and asserts exactly one result. Would fail if the outer join multiplies rows per tag.
- **Issue #5:** `tests/test_playlists.py::test_playlist_returns_all_songs` — asserts a 5-song playlist returns 5 songs. Would fail against buggy code because `songs[:-1]` drops the last track.
