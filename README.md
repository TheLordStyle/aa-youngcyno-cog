# aa-youngcyno-cog

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Alliance Auth](https://img.shields.io/badge/Alliance%20Auth-5.x-green.svg)](https://gitlab.com/allianceauth/allianceauth)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An [Alliance Auth](https://gitlab.com/allianceauth/allianceauth) Discord cog
that surfaces recently created characters with the ability to light a
cynosural field — a paranoia tool for leadership and recruiters.

Built on top of [aadiscordbot](https://github.com/pvyParts/allianceauth-discordbot)
and [allianceauth-corptools](https://github.com/Solar-Helix-Independent-Transport/allianceauth-corp-tools).

## What it does

Looks for every character that:

- Has **Cynosural Field Theory** trained to level 1 or higher (the prerequisite
  for both regular and covert cynos).
- First appears in their corporation history within the last *N* days
  (default 100). This is corptools' standard proxy for character age —
  every new EVE character auto-joins their NPC starter corp at creation.

For each match, the embed shows:

- The cyno character, their corp and alliance, and current cyno skill level.
- The main character on their auth account, if linked.
- A status line for the cyno-fitted asset check — ⚠️ with the count and
  list of `<ship type> @ <system>` entries when there's at least one hit,
  or ✅ confirming a clean result otherwise. Any hull counts — Venture,
  Stratios, Black Ops battleship, etc.
- A 🚨 highlight when the character's last-known active ship has a cyno
  module fitted right now, naming the ship type and system plus how much
  **Liquid Ozone** (the fuel a cyno actually burns) is in its cargo. ✅
  ozone means they can light at a moment's notice.
- A ⚠️ flag if the character has no auth ownership at all
  (i.e. it's in corptools via a corp roster scan but no user has claimed it).

### Example output

> **Bob McCynoAlt** `[NEWCO/-NEWA-]` — 42d old, Cyno L4
> ↳ Main: Alice Maincharacter `[GOODCO/GOOD]`
> ↳ ⚠️ **2× ship(s) with cyno fitted** — Stratios @ 4-HWWF, Venture @ Jita
> ↳ 🚨 **Currently piloting cyno-fit Stratios** — 4-HWWF (✅ 400× ozone)

## Requirements

| Component | Version |
|---|---|
| Alliance Auth | ≥ 5.0 |
| [allianceauth-discordbot](https://github.com/pvyParts/allianceauth-discordbot) | recent |
| [allianceauth-corptools](https://github.com/Solar-Helix-Independent-Transport/allianceauth-corp-tools) | ≥ 2.x |
| Database | MySQL / MariaDB |

The corptools **Skills** and **Assets** modules must be enabled and the
relevant characters need to have registered with the
`esi-skills.read_skills.v1` and `esi-assets.read_assets.v1` scopes.
Without the Assets scope the cyno-fitted-ship check will silently return
no hits.

## Install

### Production (pinned)

Add to your AA `requirements.txt`:

```text
git+https://github.com/TheLordStyle/aa-youngcyno-cog.git@v0.5.0
```

Then in `local.py`:

```python
DISCORD_BOT_COGS += [
    "aa_youngcyno.youngcyno",
]

YOUNG_CYNO_DISCORD_BOT_CHANNELS = [
    111111111111111111,   # #leadership
    222222222222222222,   # #recruiters
]
```

Rebuild and restart auth.

### Development

For iteration without rebuilding the whole stack, bind-mount a checkout
into the discordbot container and install it editable:

```bash
docker compose exec allianceauth_discordbot \
    pip install -e /opt/cogs/aa-youngcyno-cog

docker compose restart allianceauth_discordbot
```

Note that editable installs don't survive a `docker compose down` and
rebuild — production state always returns to whatever's pinned in
`requirements.txt`.

## Settings

| Setting | Default | Description |
|---|---|---|
| `YOUNG_CYNO_DISCORD_BOT_CHANNELS` | `[]` | List of Discord channel IDs where the command works. Empty list = blocked everywhere. |

## Usage

### Bulk scan

Find every cyno-capable character younger than *N* days:

```text
!youngcyno          # default: characters younger than 100 days
!youngcyno 30       # characters younger than 30 days
```

```text
/youngcyno
/youngcyno days:30
```

### Single-character lookup

Run the same per-character report against a specific named character,
ignoring the cyno-skill and age filters (so you can investigate anyone,
not just young cyno-trained chars):

```text
!cynocheck Bob McCynoAlt
!cynocheck +alts Bob McCynoAlt           # also report every linked alt
```

```text
/cynocheck character:Bob McCynoAlt
/cynocheck character:Bob McCynoAlt include_siblings:true
```

With `+alts` / `include_siblings:true`, the report covers every other
character registered to the same Alliance Auth user account, not just
the named character. Useful for "show me the whole stable, not just this
one alt".

### System lookup

List every character with at least one cyno-fit ship in a given solar
system — whether they're currently piloting one of those ships there or
it's parked in a station / citadel in that system:

```text
!cynosystem Jita
!cynosystem 4-HWWF
```

```text
/cynosystem system:Jita
```

The per-character ship list in the output is scoped to ships in the
requested system; the 🚨 currently-piloting line, when shown, still
reports wherever the character is *actually* flying right now (which may
or may not be the requested system).

All commands require the AA permission `corptools.view_characteraudit`.
Outside the allow-listed channels the prefix commands react with 👎 and
the slash commands respond with an ephemeral error.

## How it works

The cog runs a single SQL query joining:

- `corptools_skill` filtered to type ID 21603 (Cynosural Field Theory) at
  active level ≥ 1
- `corptools_corporationhistory` aggregated to `MIN(start_date)` per
  character (the same age proxy used by corptools' built-in
  `CharacterAgeFilter`)
- `corptools_characterasset` self-joined to find **any ship hull** that
  has a cyno generator fitted in a high slot (`location_flag LIKE
  'HiSlot%'`, `type_id IN (21096, 52694, 28646)` — regular, industrial,
  covert)
- `eve_sde_itemtype` to resolve ship type IDs to human names
- `corptools_evelocation` → `eve_sde_solarsystem` to resolve where each
  flagged ship is parked
- `corptools_characterlocation` (joined to the same location/system
  tables) to detect the character's last-known active ship + system
- Two scalar subqueries against `corptools_characterasset` keyed on
  `cl.current_ship_unique` (the unique item_id of that exact ship) to
  count cyno modules in any high slot and sum Liquid Ozone in the
  current ship's cargo bay
- Alliance Auth's `CharacterOwnership` / `UserProfile` to resolve the main
  character and auth user

## Caveats

- **Update lag.** corptools updates each character roughly once per day by
  default. For a real-time check on a specific suspect, use corptools' force
  refresh in the UI.
- **Brand new characters with no corp history yet** won't match this query.
  They're rare, but if you want to catch them too, the cog could be extended
  to fall back to ESI's public `/characters/{id}/` endpoint.
- **Alpha vs Omega.** This filter uses `active_skill_level ≥ 1`, which
  reflects what the character can use under their current clone state.
  Swap to `trained_skill_level` in the query for the more paranoid check
  that catches alpha-clone characters who've trained the skill but can't
  currently use it.

## Contributing

Bug reports and PRs welcome. Please open an issue first for anything beyond
trivial fixes so we can talk about it.

## License

MIT — see [LICENSE](LICENSE).
