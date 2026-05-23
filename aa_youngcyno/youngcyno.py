"""YoungCyno: surface cyno-capable characters."""
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

CYNO_MODULE_TYPE_IDS = (
    21096,  # Cynosural Field Generator I        — regular
    52694,  # Industrial Cynosural Field Generator I
    28646,  # Covert Cynosural Field Generator I — black ops / covert ops
)
_CYNO_PLACEHOLDERS = ", ".join(["%s"] * len(CYNO_MODULE_TYPE_IDS))


def _sql(*, filter_block: str, asset_join: str, asset_extra: str, tail: str) -> str:
    """Compose a query variant. The SELECT clause + most of the JOIN chain
    is shared; the variants only differ in which characters they target
    (filter_block, tail) and whether the cyno-asset subquery restricts to
    ships in a given system (asset_extra) and excludes characters with no
    such ship (asset_join = INNER vs LEFT).

    `LIKE 'HiSlot%%'` — the `%` is doubled because Django's cursor.execute
    treats the SQL string as a printf-style template.
    """
    return f"""
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
{filter_block}
{asset_join} (
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
      {asset_extra}
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
{tail}
"""


SQL_BULK = _sql(
    filter_block="""\
JOIN corptools_skill s
    ON s.character_id = ca.id
   AND s.skill_id = %s
   AND s.active_skill_level >= 1
JOIN (
    SELECT character_id, MIN(start_date) AS first_seen
    FROM corptools_corporationhistory
    GROUP BY character_id
    HAVING first_seen >= DATE_SUB(NOW(), INTERVAL %s DAY)
) ch ON ch.character_id = ca.id""",
    asset_join="LEFT JOIN",
    asset_extra="",
    tail="ORDER BY ch.first_seen DESC",
)

_DISPLAY_ONLY_SKILL_HISTORY = """\
LEFT JOIN corptools_skill s
    ON s.character_id = ca.id
   AND s.skill_id = %s
LEFT JOIN (
    SELECT character_id, MIN(start_date) AS first_seen
    FROM corptools_corporationhistory
    GROUP BY character_id
) ch ON ch.character_id = ca.id"""

SQL_SINGLE = _sql(
    filter_block=_DISPLAY_ONLY_SKILL_HISTORY,
    asset_join="LEFT JOIN",
    asset_extra="",
    tail="WHERE ec.character_name = %s",
)

SQL_SINGLE_WITH_SIBLINGS = _sql(
    filter_block=_DISPLAY_ONLY_SKILL_HISTORY,
    asset_join="LEFT JOIN",
    asset_extra="",
    tail="""\
WHERE ec.id IN (
    -- the named character (always, even if not linked to an auth user)
    SELECT id FROM eveonline_evecharacter WHERE character_name = %s
    UNION
    -- every other character linked to the same auth user, if any
    SELECT co.character_id
    FROM authentication_characterownership co
    WHERE co.user_id = (
        SELECT my_co.user_id
        FROM authentication_characterownership my_co
        JOIN eveonline_evecharacter target ON target.id = my_co.character_id
        WHERE target.character_name = %s
    )
)
ORDER BY ec.character_name""",
)

SQL_SYSTEM = _sql(
    filter_block=_DISPLAY_ONLY_SKILL_HISTORY,
    asset_join="INNER JOIN",  # drop chars with no cyno-fit ship in this system
    asset_extra="AND sys.name = %s",
    tail="ORDER BY ec.character_name",
)


def _cur_cyno_and_ozone_params():
    return [*CYNO_MODULE_TYPE_IDS, LIQUID_OZONE_TYPE_ID]


def _run_bulk_query(days: int):
    with connection.cursor() as cur:
        cur.execute(SQL_BULK, [
            *_cur_cyno_and_ozone_params(),
            CYNO_SKILL_ID,
            days,
            *CYNO_MODULE_TYPE_IDS,
        ])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _run_single_query(name: str, include_siblings: bool = False):
    sql = SQL_SINGLE_WITH_SIBLINGS if include_siblings else SQL_SINGLE
    params = [
        *_cur_cyno_and_ozone_params(),
        CYNO_SKILL_ID,
        *CYNO_MODULE_TYPE_IDS,
    ]
    if include_siblings:
        params += [name, name]
    else:
        params += [name]
    with connection.cursor() as cur:
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _run_system_query(system: str):
    with connection.cursor() as cur:
        cur.execute(SQL_SYSTEM, [
            *_cur_cyno_and_ozone_params(),
            CYNO_SKILL_ID,
            *CYNO_MODULE_TYPE_IDS,
            system,
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
        """Usage:
            !cynocheck <character name>
            !cynocheck +alts <character name>   (also report linked alts)
        """
        if not _channel_allowed(ctx.message.channel.id):
            return await ctx.message.add_reaction(chr(0x1F44E))
        if not character:
            return await ctx.message.reply(
                "Usage: `!cynocheck <name>` or `!cynocheck +alts <name>`"
            )

        text = character.strip()
        include_siblings = False
        if text.lower().startswith("+alts"):
            include_siblings = True
            text = text.split(None, 1)[1].strip() if " " in text else ""
        if not text:
            return await ctx.message.reply(
                "Usage: `!cynocheck +alts <name>`"
            )

        rows = await self._do_single(text, include_siblings)
        title = self._single_title(text, include_siblings)
        empty = self._single_empty(text, include_siblings)
        for embed in _build_embeds(rows, title, empty_message=empty):
            await ctx.message.reply(embed=embed)

    @commands.slash_command(name='cynocheck', guild_ids=get_all_servers())
    @option("character", description="Exact character name", required=True)
    @option(
        "include_siblings",
        description="Also report all other characters linked to the same AA user",
        required=False,
        default=False,
    )
    async def slash_cynocheck(self, ctx, character: str, include_siblings: bool = False):
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
        rows = await self._do_single(name, include_siblings)
        title = self._single_title(name, include_siblings)
        empty = self._single_empty(name, include_siblings)
        embeds = _build_embeds(rows, title, empty_message=empty)
        await ctx.respond(embed=embeds[0])
        for e in embeds[1:]:
            await ctx.followup.send(embed=e)

    async def _do_single(self, name: str, include_siblings: bool):
        return _run_single_query(name, include_siblings=include_siblings)

    @staticmethod
    def _single_title(name: str, include_siblings: bool) -> str:
        return (
            f"Cyno report: {name} + linked alts"
            if include_siblings else f"Cyno report: {name}"
        )

    @staticmethod
    def _single_empty(name: str, include_siblings: bool) -> str:
        if include_siblings:
            return (
                f"`{name}` not found in corptools, and no linked alts were "
                f"found either."
            )
        return f"`{name}` not found in corptools (unknown character or not yet scanned)."

    # ---- system lookup ----------------------------------------------------

    @commands.command(pass_context=True)
    @sender_has_perm('corptools.view_characteraudit')
    async def cynosystem(self, ctx, *, system: str = None):
        """List every character with a cyno-fit ship currently in the given
        system (whether they're sitting in it or it's parked in a station
        or citadel there).
        """
        if not _channel_allowed(ctx.message.channel.id):
            return await ctx.message.add_reaction(chr(0x1F44E))
        if not system:
            return await ctx.message.reply(
                "Usage: `!cynosystem <system name>`"
            )

        name = system.strip()
        rows = _run_system_query(name)
        title = f"Cyno-fit ships in {name}"
        empty = f"No characters have a cyno-fit ship in `{name}`."
        for embed in _build_embeds(rows, title, empty_message=empty):
            await ctx.message.reply(embed=embed)

    @commands.slash_command(name='cynosystem', guild_ids=get_all_servers())
    @option("system", description="Exact system name (e.g. Jita, 4-HWWF)", required=True)
    async def slash_cynosystem(self, ctx, system: str):
        try:
            in_channels(ctx.channel.id, getattr(
                settings, "YOUNG_CYNO_DISCORD_BOT_CHANNELS", []
            ))
        except commands.MissingPermissions:
            return await ctx.respond(
                "This command isn't available in this channel.", ephemeral=True
            )

        name = system.strip()
        await ctx.defer()
        rows = _run_system_query(name)
        title = f"Cyno-fit ships in {name}"
        empty = f"No characters have a cyno-fit ship in `{name}`."
        embeds = _build_embeds(rows, title, empty_message=empty)
        await ctx.respond(embed=embeds[0])
        for e in embeds[1:]:
            await ctx.followup.send(embed=e)


def setup(bot):
    bot.add_cog(YoungCyno(bot))
