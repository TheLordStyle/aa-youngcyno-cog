# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-05-16

### Added
- New `!cynosystem <name>` / `/cynosystem system:<name>` command. Lists
  every character with at least one cyno-fit ship in that solar system,
  whether they're currently sitting in one of those ships there or it's
  parked in a station / citadel in that system. The per-character ship
  list is scoped to that system; the current-ship line still reports
  wherever they're actually flying right now.
- `cynocheck` gained an `include_siblings` option (default false). When
  enabled, the report includes every other character linked to the same
  Alliance Auth user via `authentication_characterownership.user_id`, in
  addition to the named character.
- `!cynocheck +alts <name>` shortcut for the prefix-command form of the
  siblings option.

### Changed
- The SQL is now built from one shared template (`_sql(...)`) with three
  variation points: `filter_block` (skill/age JOIN shape), `asset_join`
  (`LEFT JOIN` vs `INNER JOIN` for the cyno-asset subquery), and
  `asset_extra` (the in-subquery system filter). The four variants
  (`SQL_BULK`, `SQL_SINGLE`, `SQL_SINGLE_WITH_SIBLINGS`, `SQL_SYSTEM`)
  reuse the same SELECT clause and the same downstream JOIN tail.

## [0.4.0] - 2026-05-16

### Added
- New `!cynocheck <name>` / `/cynocheck character:<name>` command that
  runs the same per-character report against a single named character.
  Unlike the bulk `youngcyno` scan, this one drops the cyno-skill and
  age filters so you can also investigate characters that don't match
  either pattern (e.g. someone who's clearly cyno-trained on paper but
  hasn't been caught by the bulk filter yet, or a suspicious old account).
- The cyno-skill level and character age render gracefully when missing
  (`no Cyno skill`, `age unknown`) so single-char lookups against
  not-yet-cyno-trained characters still produce a clean report.

### Changed
- Refactored the SQL into a shared SELECT/JOIN tail plus two thin
  filter blocks (bulk: skill+age JOINs; single: name WHERE). Both modes
  reuse the same per-row formatter and embed builder.

## [0.3.2] - 2026-05-16

### Performance
- The character-age threshold is now applied inside the corp-history
  aggregate via `HAVING first_seen >= DATE_SUB(NOW(), INTERVAL %s DAY)`
  rather than the outer `WHERE`. The derived table now materializes only
  the young chars instead of every character in the DB, so all
  downstream LEFT JOINs and the per-row scalar subqueries operate on a
  pre-pruned set.

## [0.3.1] - 2026-05-16

### Performance
- The cyno-fitted-ship subquery is now driven from the cyno-module side
  instead of the ship side. The 0.3.0 shape (`FROM corptools_characterasset
  ship WHERE EXISTS (...)`) had no selective outer filter, so MariaDB scanned
  every ship-asset row and re-evaluated the EXISTS against the same table
  for each one — expensive on any non-trivial asset table. The inverted
  shape (`FROM corptools_characterasset cyno_mod ... JOIN
  corptools_characterasset ship ON ship.item_id = cyno_mod.location_id`)
  starts from a tiny working set (matched by `type_id IN (cyno modules)
  AND location_flag LIKE 'HiSlot%'`) and joins up to the parent ship per
  matching cyno. Same output, dramatically fewer rows touched.
- `COUNT(*)` / `GROUP_CONCAT(...)` swapped for
  `COUNT(DISTINCT ship.item_id)` / `GROUP_CONCAT(DISTINCT ...)` to
  deduplicate in the edge case of more than one cyno module fitted to the
  same ship.

## [0.3.0] - 2026-05-16

### Changed
- Cyno-fitted detection is no longer Venture-specific. The asset check now
  matches **any ship hull** that has a cyno generator fitted in a high
  slot, and reports the ship type alongside its system (e.g.
  `Stratios @ 4-HWWF, Venture @ Jita`).
- The currently-piloting highlight now fires whenever the character's
  active ship has a cyno fitted, regardless of hull. The line names the
  ship type and keeps the Liquid Ozone status indicator.

### Removed
- The Mining Frigate skill line. It only mattered when the cog was
  Venture-specific; now that any hull counts, hull-flying skill detail
  isn't actionable.
- `MINING_FRIGATE_SKILL_ID` and `VENTURE_TYPE_ID` constants and the
  Mining Frigate JOIN against `corptools_skill`.

### Added
- JOIN against `eve_sde_itemtype` to resolve ship type IDs to human names
  for both the asset check and the currently-piloting line.

## [0.2.1] - 2026-05-16

### Changed
- The Venture-with-cyno-fitted asset check now always renders a line per
  character, so a clean result is explicit instead of silent. Hits still
  show the ⚠️ count + systems; misses show `✅ no Venture+cyno fitted
  in any hangar`.

### Removed
- The standalone `↳ User: <auth_user>` line. It was a holdover from the
  Discord-mention output and the same identity is already conveyed by
  the `↳ Main:` line in every case where a user is linked.

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
- The 🚨 line now also reports whether **that specific Venture** has a
  cyno module fitted (any of the three types) and the quantity of
  `Liquid Ozone` (type 16273) in its cargo — the actual consumable
  required to light a cyno alongside the module. A character in a Venture
  with both ✅ is the fully-armed signal.

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
