"""YoungCyno: surface recently created cyno-capable characters."""
import logging

from aadiscordbot.app_settings import get_all_servers
from aadiscordbot.cogs.utils.decorators import in_channels, sender_has_perm
from discord import option
from discord.embeds import Embed
from discord.ext import commands

from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)

CYNO_SKILL_ID = 21603            # Cynosural Field Theory
LIQUID_OZONE_TYPE_ID = 16273     # required in cargo to actually light a cyno

# Every high-slot module that lights a cyno.
CYNO_MODULE_TYPE_IDS = (
    21096,  # Cynosural Field Generator I        — regular
    52694,  # Industrial Cynosural Field Generator I
    28646,  # Covert Cynosural Field Generator I — black ops / covert ops
)
_CYNO_PLACEHOLDERS = ", ".join(["%s"] * len(CYNO_MODULE_TYPE_IDS))

# Shared SELECT clause + JOIN tail used by both bulk and single-char queries.
# Kept as one string so the two query variants only differ in their filter
# JOINs/WHEREs and stay visually parallel.
#
# `LIKE 'HiSlot%%'` — the `%` is doubled because Django's cursor.execute
# treats the SQL string as a printf-style template for parameter substitution.
_SELECT_AND_TAIL = f"""
SELECT
    ec.character_name              AS cyno_char,
    ec.corporation_ticker          AS cyno_corp,
    ec.alliance_ticker             AS cyno_ally,
    main.character_name            AS main_char,
    main.corporation_ticker        AS main_corp,
    main.alliance_ticker           AS main_ally,
    au.username                    AS auth_user,
    DATEDIFF(NOW(), ch.first_seen) AS days_old,
    s.active_skill_level           AS cyno_lvl,
    sc.fitted_count                AS ship_cyno_count,
    sc.ships                       AS ship_cyno_list,
    cur_ship_type.name             AS current_ship_name,
    cl_sys.name                    AS current_system,
    (
        SELECT COUNT(*)
        FROM corptools_characterasset cur_m
        WHERE cur_m.character_id = ca.id
          AND cur_m.location_id  = cl.current_ship_unique
          AND cur_m.location_flag LIKE 'HiSlot%%'
          AND cur_m.type_id IN ({_CYNO_PLACEHOLDERS})
    )                              AS current_cyno_count,
    (
        SELECT COALESCE(SUM(cur_o.quantity), 0)
        FROM corptools_characterasset cur_o
        WHERE cur_o.character_id = ca.id
          AND cur_o.location_id  = cl.current_ship_unique
          AND cur_o.location_flag = 'Cargo'
          AND cur_o.type_id = %s
    )                              AS current_ozone_qty
FROM corptools_characteraudit ca
JOIN eveonline_evecharacter ec
    ON ec.id = ca.character_id
{{filter_block}}
-- Driven from the cyno-module side (very selective: only a handful of
-- type_ids), then joined up to the parent ship. The previous shape — scan
-- every ship-asset row then EXISTS — exploded on large asset tables.
LEFT JOIN (
    SELECT
        cyno_mod.character_id,
        COUNT(DISTINCT ship.item_id) AS fitted_count,
        GROUP_CONCAT(DISTINCT CONCAT(
            COALESCE(ship_type.name, CONCAT('type ', ship.type_id)),
            ' @ ',
            COALESCE(sys.name, 'unknown')
        ) SEPARATOR ', ') AS ships
    FROM corptools_characterasset cyno_mod
    JOIN corptools_characterasset ship
        ON ship.character_id = cyno_mod.character_id
       AND ship.item_id      = cyno_mod.location_id
    LEFT JOIN eve_sde_itemtype ship_type
        ON ship_type.id = ship.type_id
    LEFT JOIN corptools_evelocation loc
        ON loc.location_id = ship.location_name_id
    LEFT JOIN eve_sde_solarsystem sys
        ON sys.id = loc.system_id
    WHERE cyno_mod.location_flag LIKE 'HiSlot%%'
      AND cyno_mod.type_id IN ({_CYNO_PLACEHOLDERS})
    GROUP BY cyno_mod.character_id
) sc ON sc.character_id = ca.id
LEFT JOIN corptools_characterlocation cl
    ON cl.character_id = ca.id
LEFT JOIN eve_sde_itemtype cur_ship_type
    ON cur_ship_type.id = cl.current_ship_id
LEFT JOIN corptools_evelocation cl_loc
    ON cl_loc.location_id = cl.current_location_id
LEFT JOIN eve_sde_solarsystem cl_sys
    ON cl_sys.id = cl_loc.system_id
LEFT JOIN authentication_characterownership co
    ON co.character_id = ec.id
LEFT JOIN auth_user au
    ON au.id = co.user_id
LEFT JOIN authentication_userprofile up
    ON up.user_id = co.user_id
LEFT JOIN eveonline_evecharacter main
    ON main.id = up.main_character_id
{{tail}}
"""

# Bulk: require cyno skill + corp-history age threshold.
SQL_BULK = _SELECT_AND_TAIL.format(
    filter_block="""\
JOIN corptools_skill s
    ON s.character_id = ca.id
   AND s.skill_id = %s
   AND s.active_skill_level >= 1
JOIN (
    -- Apply the age threshold inside the aggregate so the joined-back set
    -- is just "young chars", not "every char in the DB". Without this, the
    -- subquery propagates one row per character into every downstream
    -- LEFT JOIN and scalar subquery before the outer WHERE prunes them.
    SELECT character_id, MIN(start_date) AS first_seen
    FROM corptools_corporationhistory
    GROUP BY character_id
    HAVING first_seen >= DATE_SUB(NOW(), INTERVAL %s DAY)
) ch ON ch.character_id = ca.id""",
    tail="ORDER BY ch.first_seen DESC",
)

# Single: no skill filter (so non-cyno-skilled chars still surface for
# investigation), no age filter, just match by character name.
SQL_SINGLE = _SELECT_AND_TAIL.format(
    filter_block="""\
LEFT JOIN corptools_skill s
    ON s.character_id = ca.id
   AND s.skill_id = %s
LEFT JOIN (
    SELECT character_id, MIN(start_date) AS first_seen
    FROM corptools_corporationhistory
    GROUP BY character_id
) ch ON ch.character_id = ca.id""",
    tail="WHERE ec.character_name = %s",
)


def _run_bulk_query(days: int):
    with connection.cursor() as cur:
        # Textual %s order in SQL_BULK:
        #   1. current_cyno_count scalar subquery IN (...) → 3
        #   2. current_ozone_qty  scalar subquery  = %s    → 1
        #   3. cyno skill JOIN    skill_id = %s            → 1
        #   4. corp history HAVING first_seen days         → 1
        #   5. ship_cyno subquery IN (...)                 → 3
        cur.execute(SQL_BULK, [
            *CYNO_MODULE_TYPE_IDS,
            LIQUID_OZONE_TYPE_ID,
            CYNO_SKILL_ID,
            days,
            *CYNO_MODULE_TYPE_IDS,
        ])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _run_single_query(name: str):
    with connection.cursor() as cur:
        # Textual %s order in SQL_SINGLE:
        #   1. current_cyno_count scalar subquery IN (...) → 3
        #   2. current_ozone_qty  scalar subquery  = %s    → 1
        #   3. cyno skill LEFT JOIN skill_id = %s          → 1
        #   4. ship_cyno subquery IN (...)                 → 3
        #   5. WHERE ec.character_name = %s                → 1
        cur.execute(SQL_SINGLE, [
            *CYNO_MODULE_TYPE_IDS,
            LIQUID_OZONE_TYPE_ID,
            CYNO_SKILL_ID,
            *CYNO_MODULE_TYPE_IDS,
            name,
        ])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _format_row(r: dict) -> str:
    cyno_ally = f"/{r['cyno_ally']}" if r['cyno_ally'] else ""
    age_part = (
        f"{r['days_old']}d old" if r['days_old'] is not None else "age unknown"
    )
    cyno_part = (
        f"Cyno L{r['cyno_lvl']}" if r['cyno_lvl'] is not None else "no Cyno skill"
    )
    head = (
        f"**{r['cyno_char']}** "
        f"`[{r['cyno_corp'] or '-'}{cyno_ally}]` "
        f"— {age_part}, {cyno_part}"
    )

    if r['main_char']:
        main_ally = f"/{r['main_ally']}" if r['main_ally'] else ""
        if r['main_char'] == r['cyno_char']:
            main_line = "↳ Main: *this character*"
        else:
            main_line = (
                f"↳ Main: {r['main_char']} "
                f"`[{r['main_corp'] or '-'}{main_ally}]`"
            )
    elif r['auth_user']:
        main_line = "↳ Main: *(no main set)*"
    else:
        main_line = "↳ ⚠️ **No auth ownership** — orphan / corp-roster only"

    count = r['ship_cyno_count'] or 0
    if count:
        ships = r['ship_cyno_list'] or 'unknown'
        cyno_asset_line = (
            f"↳ ⚠️ **{count}× ship(s) with cyno fitted** — {ships}"
        )
    else:
        cyno_asset_line = "↳ ✅ no ships with cyno fitted in any hangar"

    if (r['current_cyno_count'] or 0):
        ship_name = r['current_ship_name'] or 'unknown ship'
        where = r['current_system'] or 'unknown system'
        ozone = int(r['current_ozone_qty'] or 0)
        ozone_mark = f"✅ {ozone}× ozone" if ozone else "❌ no ozone"
        current_ship_line = (
            f"↳ 🚨 **Currently piloting cyno-fit {ship_name}** "
            f"— {where} ({ozone_mark})"
        )
    else:
        current_ship_line = ""

    return "\n".join(filter(None, [
        head, main_line, cyno_asset_line, current_ship_line,
    ]))


def _build_embeds(rows, title: str, empty_message: str = "No matches."):
    if not rows:
        return [Embed(title=title, description=empty_message, colour=0x2ECC71)]

    blocks = [_format_row(r) for r in rows]
    embeds, buf, size = [], [], 0
    for block in blocks:
        if size + len(block) + 2 > 3900:
            embeds.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(block)
        size += len(block) + 2
    if buf:
        embeds.append("\n\n".join(buf))

    out, total = [], len(rows)
    for i, desc in enumerate(embeds, 1):
        page_title = title + (f" ({i}/{len(embeds)})" if len(embeds) > 1 else "")
        e = Embed(title=page_title, description=desc, colour=0xE67E22)
        if i == 1 and total > 1:
            e.set_footer(text=f"{total} match(es)")
        out.append(e)
    return out


def _channel_allowed(channel_id: int) -> bool:
    return channel_id in getattr(
        settings, "YOUNG_CYNO_DISCORD_BOT_CHANNELS", []
    )


class YoungCyno(commands.Cog):
    """Identify cyno-capable characters."""

    def __init__(self, bot):
        self.bot = bot

    # ---- bulk: youngest cyno chars ----------------------------------------

    @commands.command(pass_context=True)
    @sender_has_perm('corptools.view_characteraudit')
    async def youngcyno(self, ctx, days: int = 100):
        if not _channel_allowed(ctx.message.channel.id):
            return await ctx.message.add_reaction(chr(0x1F44E))

        days = max(1, min(days, 365))
        rows = _run_bulk_query(days)
        title = f"Cyno-capable characters younger than {days} days"
        for embed in _build_embeds(rows, title):
            await ctx.message.reply(embed=embed)

    @commands.slash_command(name='youngcyno', guild_ids=get_all_servers())
    @option("days", description="Max character age in days (default 100)", required=False)
    async def slash_youngcyno(self, ctx, days: int = 100):
        try:
            in_channels(ctx.channel.id, getattr(
                settings, "YOUNG_CYNO_DISCORD_BOT_CHANNELS", []
            ))
        except commands.MissingPermissions:
            return await ctx.respond(
                "This command isn't available in this channel.", ephemeral=True
            )

        days = max(1, min(days, 365))
        await ctx.defer()
        rows = _run_bulk_query(days)
        title = f"Cyno-capable characters younger than {days} days"
        embeds = _build_embeds(rows, title)
        await ctx.respond(embed=embeds[0])
        for e in embeds[1:]:
            await ctx.followup.send(embed=e)

    # ---- single-char lookup -----------------------------------------------

    @commands.command(pass_context=True)
    @sender_has_perm('corptools.view_characteraudit')
    async def cynocheck(self, ctx, *, character: str = None):
        if not _channel_allowed(ctx.message.channel.id):
            return await ctx.message.add_reaction(chr(0x1F44E))
        if not character:
            return await ctx.message.reply(
                "Usage: `!cynocheck <character name>`"
            )

        name = character.strip()
        rows = _run_single_query(name)
        title = f"Cyno report: {name}"
        empty = f"`{name}` not found in corptools (unknown character or not yet scanned)."
        for embed in _build_embeds(rows, title, empty_message=empty):
            await ctx.message.reply(embed=embed)

    @commands.slash_command(name='cynocheck', guild_ids=get_all_servers())
    @option("character", description="Exact character name", required=True)
    async def slash_cynocheck(self, ctx, character: str):
        try:
            in_channels(ctx.channel.id, getattr(
                settings, "YOUNG_CYNO_DISCORD_BOT_CHANNELS", []
            ))
        except commands.MissingPermissions:
            return await ctx.respond(
                "This command isn't available in this channel.", ephemeral=True
            )

        name = character.strip()
        await ctx.defer()
        rows = _run_single_query(name)
        title = f"Cyno report: {name}"
        empty = f"`{name}` not found in corptools (unknown character or not yet scanned)."
        embeds = _build_embeds(rows, title, empty_message=empty)
        await ctx.respond(embed=embeds[0])
        for e in embeds[1:]:
            await ctx.followup.send(embed=e)


def setup(bot):
    bot.add_cog(YoungCyno(bot))
