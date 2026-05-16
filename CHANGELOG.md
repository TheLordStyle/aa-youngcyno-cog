# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-05-16

### Added
- "Venture" line per match showing Mining Frigate skill level if trained
  (the prerequisite for flying a Venture, the classic cheap cyno hull).
- Asset scan: flags characters who currently have one or more Ventures
  with a cyno generator actually fitted in a high slot (any of: regular
  `Cynosural Field Generator I`, `Industrial Cynosural Field Generator I`,
  or `Covert Cynosural Field Generator I`), with the system(s) the ship
  is parked in.
- 🚨 highlight when the character's last-known active ship (per
  `corptools_characterlocation`) is a Venture, including the system
  they're currently in.

### Removed
- Discord user mention (`<@uid>`) from the output. The auth username is
  still shown; look the user up in Discord manually if you need to ping
  them.

### Changed
- SQL no longer joins `discord_discorduser`. Skill, asset, and
  current-location joins added against `corptools_skill`,
  `corptools_characterasset`, `corptools_characterlocation`,
  `corptools_evelocation`, and `eve_sde_solarsystem`.

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
