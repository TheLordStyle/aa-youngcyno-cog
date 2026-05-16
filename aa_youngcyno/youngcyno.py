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
MINING_FRIGATE_SKILL_ID = 32918  # required to fly a Venture
VENTURE_TYPE_ID = 32880
LIQUID_OZONE_TYPE_ID = 16273     # required in cargo to actually light a cyno

# Every high-slot module that lights a cyno. Covert is included even though
# it can't physically fit on a Venture — the asset-fitted self-join filters
# by what's actually mounted, so listing it is harmless and future-proofs
# the check if we ever broaden the hull filter beyond Venture.
CYNO_MODULE_TYPE_IDS = (
    21096,  # Cynosural Field Generator I        — regular
    52694,  # Industrial Cynosural Field Generator I
    28646,  # Covert Cynosural Field Generator I — black ops / covert ops
)
_CYNO_PLACEHOLDERS = ", ".join(["%s"] * len(CYNO_MODULE_TYPE_IDS))

# `LIKE 'HiSlot%%'` — the `%` is doubled because Django's cursor.execute
# treats the SQL string as a printf-style template for parameter substitution.
SQL = f"""
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
    mf.active_skill_level          AS venture_lvl,
    vc.fitted_count                AS venture_cyno_count,
    vc.systems                     AS venture_cyno_systems,
    cl.current_ship_id             AS current_ship_type,
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
JOIN corptools_skill s
    ON s.character_id = ca.id
   AND s.skill_id = %s
   AND s.active_skill_level >= 1
LEFT JOIN corptools_skill mf
    ON mf.character_id = ca.id
   AND mf.skill_id = %s
   AND mf.active_skill_level >= 1
JOIN (
    SELECT character_id, MIN(start_date) AS first_seen
    FROM corptools_corporationhistory
    GROUP BY character_id
) ch ON ch.character_id = ca.id
LEFT JOIN (
    SELECT
        v.character_id,
        COUNT(*) AS fitted_count,
        GROUP_CONCAT(COALESCE(sys.name, 'unknown') ORDER BY sys.name SEPARATOR ', ') AS systems
    FROM corptools_characterasset v
    JOIN corptools_characterasset m
        ON m.character_id = v.character_id
       AND m.location_id  = v.item_id
       AND m.location_flag LIKE 'HiSlot%%'
       AND m.type_id      IN ({_CYNO_PLACEHOLDERS})
    LEFT JOIN corptools_evelocation loc
        ON loc.location_id = v.location_name_id
    LEFT JOIN eve_sde_solarsystem sys
        ON sys.id = loc.system_id
    WHERE v.type_id = %s
    GROUP BY v.character_id
) vc ON vc.character_id = ca.id
LEFT JOIN corptools_characterlocation cl
    ON cl.character_id = ca.id
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
WHERE ch.first_seen >= DATE_SUB(NOW(), INTERVAL %s DAY)
ORDER BY ch.first_seen DESC
"""


def _run_query(days: int):
    with connection.cursor() as cur:
        # Param order matches textual order of %s in SQL:
        #   1. current_cyno_count scalar subquery  IN (...)      → 3
        #   2. current_ozone_qty  scalar subquery  type_id = %s  → 1
        #   3. cyno skill JOIN    skill_id = %s                  → 1
        #   4. mining frigate JOIN skill_id = %s                 → 1
        #   5. venture_cyno subquery IN (...) + v.type_id = %s   → 3 + 1
        #   6. WHERE first_seen >= NOW() - INTERVAL %s DAY       → 1
        cur.execute(SQL, [
            *CYNO_MODULE_TYPE_IDS,
            LIQUID_OZONE_TYPE_ID,
            CYNO_SKILL_ID,
            MINING_FRIGATE_SKILL_ID,
            *CYNO_MODULE_TYPE_IDS,
            VENTURE_TYPE_ID,
            days,
        ])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _format_row(r: dict) -> str:
    cyno_ally = f"/{r['cyno_ally']}" if r['cyno_ally'] else ""
    head = (
        f"**{r['cyno_char']}** "
        f"`[{r['cyno_corp'] or '-'}{cyno_ally}]` "
        f"— {r['days_old']}d old, Cyno L{r['cyno_lvl']}"
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

    venture_line = (
        f"↳ Venture: Mining Frigate L{r['venture_lvl']}"
        if r['venture_lvl'] else ""
    )

    count = r['venture_cyno_count'] or 0
    if count:
        systems = r['venture_cyno_systems'] or 'unknown'
        cyno_asset_line = (
            f"↳ ⚠️ **{count}× Venture with cyno fitted** — {systems}"
        )
    else:
        cyno_asset_line = "↳ ✅ no Venture+cyno fitted in any hangar"

    if r['current_ship_type'] == VENTURE_TYPE_ID:
        where = r['current_system'] or 'unknown system'
        cyno_mark = "✅ cyno" if (r['current_cyno_count'] or 0) else "❌ no cyno"
        ozone = int(r['current_ozone_qty'] or 0)
        ozone_mark = f"✅ {ozone}× ozone" if ozone else "❌ no ozone"
        current_ship_line = (
            f"↳ 🚨 **Currently piloting a Venture** — {where} "
            f"({cyno_mark}, {ozone_mark})"
        )
    else:
        current_ship_line = ""

    return "\n".join(filter(None, [
        head, main_line, venture_line,
        cyno_asset_line, current_ship_line,
    ]))


def _build_embeds(rows, days: int):
    if not rows:
        return [Embed(
            title=f"Cyno-capable characters younger than {days} days",
            description="No matches.",
            colour=0x2ECC71,
        )]

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
        title = f"Cyno-capable characters younger than {days} days"
        if len(embeds) > 1:
            title += f" ({i}/{len(embeds)})"
        e = Embed(title=title, description=desc, colour=0xE67E22)
        if i == 1:
            e.set_footer(text=f"{total} match(es)")
        out.append(e)
    return out


class YoungCyno(commands.Cog):
    """Identify recently created characters with cyno capability."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(pass_context=True)
    @sender_has_perm('corptools.view_characteraudit')
    async def youngcyno(self, ctx, days: int = 100):
        if ctx.message.channel.id not in getattr(
            settings, "YOUNG_CYNO_DISCORD_BOT_CHANNELS", []
        ):
            return await ctx.message.add_reaction(chr(0x1F44E))

        days = max(1, min(days, 365))
        rows = _run_query(days)
        for embed in _build_embeds(rows, days):
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
        rows = _run_query(days)
        embeds = _build_embeds(rows, days)
        await ctx.respond(embed=embeds[0])
        for e in embeds[1:]:
            await ctx.followup.send(embed=e)


def setup(bot):
    bot.add_cog(YoungCyno(bot))
