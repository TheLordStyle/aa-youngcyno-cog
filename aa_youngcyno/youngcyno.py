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

CYNO_TYPE_ID = 21603  # Cynosural Field Theory

SQL = """
SELECT
    ec.character_name              AS cyno_char,
    ec.corporation_ticker          AS cyno_corp,
    ec.alliance_ticker             AS cyno_ally,
    main.character_name            AS main_char,
    main.corporation_ticker        AS main_corp,
    main.alliance_ticker           AS main_ally,
    au.username                    AS auth_user,
    dd.uid                         AS discord_uid,
    DATEDIFF(NOW(), ch.first_seen) AS days_old,
    s.active_skill_level           AS cyno_lvl
FROM corptools_characteraudit ca
JOIN eveonline_evecharacter ec
    ON ec.id = ca.character_id
JOIN corptools_skill s
    ON s.character_id = ca.id
   AND s.skill_id = %s
   AND s.active_skill_level >= 1
JOIN (
    SELECT character_id, MIN(start_date) AS first_seen
    FROM corptools_corporationhistory
    GROUP BY character_id
) ch ON ch.character_id = ca.id
LEFT JOIN authentication_characterownership co
    ON co.character_id = ec.id
LEFT JOIN auth_user au
    ON au.id = co.user_id
LEFT JOIN authentication_userprofile up
    ON up.user_id = co.user_id
LEFT JOIN eveonline_evecharacter main
    ON main.id = up.main_character_id
LEFT JOIN discord_discorduser dd
    ON dd.user_id = co.user_id
WHERE ch.first_seen >= DATE_SUB(NOW(), INTERVAL %s DAY)
ORDER BY ch.first_seen DESC
"""


def _run_query(days: int):
    with connection.cursor() as cur:
        cur.execute(SQL, [CYNO_TYPE_ID, days])
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

    bits = []
    if r['auth_user']:
        bits.append(f"`{r['auth_user']}`")
    if r['discord_uid']:
        bits.append(f"<@{r['discord_uid']}>")
    user_line = f"↳ User: {' '.join(bits)}" if bits else ""

    return "\n".join(filter(None, [head, main_line, user_line]))


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
