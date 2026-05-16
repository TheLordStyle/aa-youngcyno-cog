# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-05-16

### Added
- Initial release.
- `!youngcyno [days]` prefix command and `/youngcyno [days]` slash command.
- Lists characters with Cynosural Field Theory ≥ L1 whose first corp history
  entry is within the given window (default 100 days).
- Output includes main character, auth username, and Discord mention where
  ownership is linked.
- Channel allow-list via `YOUNG_CYNO_DISCORD_BOT_CHANNELS`.
- Permission gating via `corptools.view_characteraudit`.
