import asyncio
import io
import logging
import os
import random
from functools import wraps
from typing import Optional

import aiohttp
import timeout_decorator
from discord import File, Game, utils
from discord.channel import TextChannel
from discord.ext import commands, tasks
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound
from texttable import Texttable

import zoom
from db import Link, session_scope
from eval_stmts import eval_stmts

logging.basicConfig(level=logging.INFO)

load_dotenv()

FURA_TEMPLATE = "fura_template.png"
FURA_TEMPLATE_OFFSET = (100, 200)
FURA_TEMPLATE_SIZE = (250, 50)
FURA_ID = int(os.environ["FURA_ID"])

GIT_COMMIT_HASH = os.environ["GIT_COMMIT_HASH"]
GIT_COMMIT_URL = f"https://github.com/beeracademy/discord-bot/commit/{GIT_COMMIT_HASH}"

if os.getenv("TEST_GUILD") == "1":
    DISCORD_TOKEN = os.environ["DISCORD_TEST_TOKEN"]
    DISCORD_GUILD = os.environ["DISCORD_TEST_GUILD"]
else:
    DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
    DISCORD_GUILD = os.environ["DISCORD_GUILD"]


AU_ID = os.environ["AU_ID"]
AU_PASSWORD = os.environ["AU_PASSWORD"]


MAX_FINISHED_GAMES = 10
MAX_DISCORD_MESSAGE_LENGTH = 2000

bot = commands.Bot("!", case_insensitive=True)


def run_with_timeout(f, fargs=[], fkwargs={}, *args, **kwargs):
    return timeout_decorator.timeout(*args, timeout_exception=TimeoutError, **kwargs)(
        f
    )(*fargs, **fkwargs)


def partition_solve(l, max_size):
    """
    Given a list of integers and a maximum bucket size,
    returns a partitioning of the list into k different buckets
    with the sum of each bucket being less or equal to the maximum size.
    The partioning is done to first minimize k and then minize
    the size difference between the smallest and biggest buckets.

    Note that this is a generalization of the multi-way partition problem.

    >>> f = lambda l, max_size: sorted(sorted(l) for l in partition_solve(l, max_size))
    >>> f([1, 2, 3], 3)
    [[1, 2], [3]]
    >>> f([5] * 3 + [4] * 5, 18)
    [[4, 4, 4, 5], [4, 4, 5, 5]]
    """

    assert 0 <= min(l)
    assert max(l) <= max_size

    n = len(l)
    total = sum(l)

    best = (n + 1, 0, [])
    global_best_possible = (div_ceil(total, max_size), int(total % max_size > 0))

    def aux(i, space_left, assignments):
        nonlocal best

        best_key = best[:2]

        if i == n:
            key = (len(space_left), max(space_left) - min(space_left))
            if key < best_key:
                best = key + (list(assignments),)
            return

        if best_key == global_best_possible:
            return

        best_possible = (len(space_left), 0)
        if best_possible >= best_key:
            return

        for j in range(len(space_left) + 1):
            if j == len(space_left):
                space_left.append(max_size)

            if space_left[j] >= l[i]:
                space_left[j] -= l[i]
                assignments.append(j)
                aux(i + 1, space_left, assignments)
                assignments.pop()
                space_left[j] += l[i]

        space_left.pop()

    aux(0, [], [])

    k, _, assignments = best
    res = [[] for _ in range(k)]
    for i, j in enumerate(assignments):
        res[j].append(l[i])

    return res


def div_ceil(a, b):
    return (a - 1) // b + 1


def get_max_font(image_draw, font_name, text, max_size):
    size = 0
    while True:
        fnt = ImageFont.truetype(font_name, size=size)
        text_size = image_draw.textsize(text, fnt)
        if text_size[0] > max_size[0] or text_size[1] > max_size[1]:
            break
        size += 1

    # Ensure size is a nonnegative integer
    if size > 0:
        size -= 1

    return ImageFont.truetype(font_name, size=size)


def get_dict(l, **kwargs):
    for d in l:
        if all(d[k] == v for k, v in kwargs.items()):
            return d

    return None


def plural(count, name):
    s = f"{count} {name}"
    if count != 1:
        s += "s"

    return s


def code_block_escape(s):
    ns = ""
    count = 0
    for c in s:
        if c == "`":
            count += 1
            if count == 3:
                ns += "\N{ZERO WIDTH JOINER}"
                count = 1
        else:
            count = 0

        ns += c
    return ns


def typing_command(*cargs, **ckwargs):
    def inner(f):
        @wraps(f)
        async def wrapper(self, ctx, *args, **kwargs):
            async with ctx.typing():
                await f(self, ctx, *args, **kwargs)

        return commands.command(*cargs, **ckwargs)(wrapper)

    return inner


class Academy(commands.Cog):
    AIOHTTP_TIMEOUT = aiohttp.ClientTimeout(total=5)

    def __init__(self, bot):
        self.bot = bot
        self.game_datas = {}
        self.update_game_datas.start()
        self.first_on_ready = True

    def cog_unload(self):
        self.update_game_datas.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        self.guild = utils.get(self.bot.guilds, name=DISCORD_GUILD)
        self.live_category = utils.get(self.guild.categories, name="Live Games")
        self.finished_category = utils.get(self.guild.categories, name="Finished Games")
        self.bot_channel = utils.get(self.guild.channels, name="bot")
        await self.update_status()
        logging.info(f"Connected as {self.bot.user}")
        if self.first_on_ready:
            await self.bot_channel.send(
                f"Just started up, running version: {GIT_COMMIT_URL}"
            )
            self.first_on_ready = False

    async def update_status(self):
        if self.game_datas:
            await self.bot.change_presence(
                activity=Game(
                    f"{plural(len(self.game_datas), 'live game')}: {list(self.game_datas.keys())}"
                )
            )
        else:
            await self.bot.change_presence(
                activity=Game(name="Waiting for new players: https://academy.beer/")
            )

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        await ctx.send(f"Got an error: {error}")

    def get_academy_id(self, discord_id):
        with session_scope() as session:
            try:
                link = session.query(Link).filter(Link.discord_id == discord_id).one()
                return link.academy_id
            except NoResultFound:
                return None

    def get_discord_id(self, academy_id):
        with session_scope() as session:
            try:
                link = session.query(Link).filter(Link.academy_id == academy_id).one()
                return link.discord_id
            except NoResultFound:
                return None

    def get_player_name(self, player_stats):
        academy_id = player_stats["id"]
        discord_id = self.get_discord_id(academy_id)
        if discord_id:
            return self.bot.get_user(discord_id).mention
        else:
            return player_stats["username"]

    def level_info(self, player_stats):
        return f"To be on level they have to have drunk {plural(player_stats['full_beers'], 'full beer')} and {plural(player_stats['extra_sips'], 'sip')}."

    def get_channel_name(self, game_id):
        return f"academy_{game_id}"

    def get_game_progress(self, game_data):
        cards = game_data["cards"]
        chug_done = 1
        if cards:
            c = cards[-1]
            if c["value"] == 14 and c["chug_duration_ms"] == None:
                chug_done = 0

        return (len(cards), chug_done)

    async def get_game_channel(self, game_id):
        channel_name = self.get_channel_name(game_id)
        return utils.get(self.guild.text_channels, name=channel_name)

    async def get_or_create_game_channel(self, game_id):
        channel = await self.get_game_channel(game_id)
        if not channel:
            channel_name = self.get_channel_name(game_id)
            user_str = ", ".join(
                p["username"] for p in self.game_datas[game_id]["player_stats"]
            )
            channel = await self.guild.create_text_channel(
                channel_name,
                category=self.live_category,
                topic=f"Game with {user_str}: https://academy.beer/games/{game_id}/",
            )
            await channel.edit(position=0)

        return channel

    async def send_in_game_channel(self, game_id, message):
        channel = await self.get_game_channel(game_id)
        await channel.send(message)

    async def post_game_update(self, game_data):
        player_stats = game_data["player_stats"]
        player_count = len(player_stats)
        card_count = len(game_data["cards"])
        total_card_count = player_count * 13
        player_index = card_count % player_count

        previous_player_name = self.get_player_name(
            player_stats[(player_index - 1) % len(player_stats)]
        )
        current_player_stats = player_stats[player_index]
        player_name = self.get_player_name(current_player_stats)

        message = ""
        is_ace_not_done = False
        if game_data["cards"]:
            card = game_data["cards"][-1]

            if card["value"] == 14:
                duration = card["chug_duration_ms"]
                if duration == None:
                    is_ace_not_done = True
                    message += (
                        f"{previous_player_name} just got an ace, so they have to chug!"
                    )
                else:
                    message += f"{previous_player_name} just finished chugging with time {duration / 1000} seconds.\n\n"
            else:
                message += f"{previous_player_name} just got a {card['value']}.\n\n"

        if not is_ace_not_done and card_count != total_card_count:
            message += f"Now it's {player_name}'s turn:\n" + self.level_info(
                player_stats[player_index]
            )

        await self.send_in_game_channel(game_data["id"], message)

    @tasks.loop(seconds=1)
    async def update_game_datas(self):
        async with aiohttp.ClientSession(
            raise_for_status=True, timeout=self.AIOHTTP_TIMEOUT
        ) as session:
            while True:
                try:
                    async with session.get(
                        f"https://academy.beer/api/games/live_games/"
                    ) as response:
                        game_ids = set(d["id"] for d in await response.json())
                        break
                except asyncio.TimeoutError:
                    logging.info(
                        "Failed to get list of live games, retrying in 1 second..."
                    )
                    await asyncio.sleep(1)

        old_game_ids = set(self.game_datas.keys())

        for game_id in game_ids:
            old_data = self.game_datas.get(game_id)
            new_data = await self.get_game_data(game_id)
            self.game_datas[game_id] = new_data
            if game_id not in old_game_ids:
                logging.info(f"New game: {game_id}")
                await self.get_or_create_game_channel(game_id)

            if not old_data or self.get_game_progress(
                old_data
            ) != self.get_game_progress(new_data):
                await self.post_game_update(new_data)

        for game_id in list(self.game_datas.keys()):
            if game_id not in game_ids:
                logging.info(f"Game is done: {game_id}")
                final_data = await self.get_game_data(game_id)
                await self.send_in_game_channel(
                    game_id,
                    f"Game has now ended.\nDescription: {final_data['description']}\nSee https://academy.beer/games/{game_id}/ for more info.",
                )
                del self.game_datas[game_id]

        live_channels = {
            await self.get_game_channel(game_id) for game_id in self.game_datas.keys()
        }
        for c in self.live_category.channels:
            if c not in live_channels:
                await c.edit(category=self.finished_category)

        finished_channels = self.finished_category.channels
        if len(finished_channels) > MAX_FINISHED_GAMES:
            for c in sorted(finished_channels, key=lambda c: c.name, reverse=True)[10:]:
                await c.delete()

        if game_ids != old_game_ids:
            await self.update_status()

    @update_game_datas.before_loop
    async def wait_until_ready(self):
        await self.bot.wait_until_ready()

    async def get_game_data(self, game_id):
        async with aiohttp.ClientSession(
            raise_for_status=True, timeout=self.AIOHTTP_TIMEOUT
        ) as session:
            while True:
                try:
                    async with session.get(
                        f"https://academy.beer/api/games/{game_id}/"
                    ) as response:
                        res = await response.json()
                        return res
                except asyncio.TimeoutError:
                    logging.info("Timed out getting game data, retrying in 1 second...")
                    await asyncio.sleep(1)

    async def get_username(self, user_id):
        async with aiohttp.ClientSession(
            raise_for_status=True, timeout=self.AIOHTTP_TIMEOUT
        ) as session:
            async with session.get(
                f"https://academy.beer/api/users/{user_id}/"
            ) as response:
                return (await response.json())["username"]

    def set_linked_account(self, discord_id, academy_id):
        with session_scope() as session:
            session.query(Link).filter(Link.discord_id == discord_id).delete()
            if academy_id:
                session.add(Link(discord_id=discord_id, academy_id=academy_id))

    @typing_command(name="link")
    async def link(self, ctx, academy_id: int):
        try:
            username = await self.get_username(academy_id)
        except:
            await ctx.send("Couldn't get user data! Does the user exist?")
            return

        username = utils.escape_markdown(username)

        discord_id = ctx.author.id

        try:
            self.set_linked_account(discord_id, academy_id)
        except IntegrityError:
            linked_discord_id = self.get_discord_id(academy_id)
            linked_mention = self.bot.get_user(linked_discord_id).mention
            await ctx.send(
                f"{ctx.author.mention} {username} is already linked to {linked_mention}!"
            )
            return

        await ctx.send(
            f"{ctx.author.mention} is now linked with {username} on academy."
        )

    @typing_command(name="unlink", aliases=["ul"])
    async def unlink(self, ctx):
        self.set_linked_account(ctx.author.id, None)
        await ctx.send(
            f"{ctx.author.mention} is now no longer linked to any academy user."
        )

    @typing_command(name="test")
    async def test(self, ctx):
        await ctx.send(f"Test {ctx.author.mention}")

    @typing_command(name="version", aliases=["v"])
    async def version(self, ctx):
        await ctx.send(f"I'm currently running the following version: {GIT_COMMIT_URL}")

    async def get_game_data_from_ctx(self, ctx, game_id):
        if game_id == None:
            if isinstance(ctx.channel, TextChannel) and ctx.channel.guild == self.guild:
                parts = ctx.channel.name.split("_")
                if len(parts) == 2 and parts[0] == "academy":
                    try:
                        game_id = int(parts[1])
                    except ValueError:
                        pass

        if game_id == None:
            await ctx.send(
                f"{ctx.author.mention} you either have to provide the game id as an argument or use the command in the associated chat."
            )
            return None

        game_data = self.game_datas.get(game_id)
        if not game_data:
            game_data = await self.get_game_data(game_id)

        return game_data

    @typing_command(name="status", aliases=["s"])
    async def status(self, ctx, game_id: Optional[int]):
        game_data = await self.get_game_data_from_ctx(ctx, game_id)
        if game_data:
            await self.post_game_update(game_data)

    @typing_command(name="level", aliases=["l"])
    async def level(self, ctx, game_id: Optional[int]):
        game_data = await self.get_game_data_from_ctx(ctx, game_id)
        if not game_data:
            return

        game_id = game_data["id"]

        academy_id = self.get_academy_id(ctx.author.id)
        if academy_id == None:
            await ctx.send(
                f"{ctx.author.mention} you need to `!link` your discord account with your academy account."
            )
            return

        player_stats = get_dict(game_data["player_stats"], id=academy_id)
        if player_stats:
            s = f"{ctx.author.mention}:\n"
            s += self.level_info(player_stats)
            await ctx.send(s)
        else:
            await ctx.send(
                f"{ctx.author.mention} doesn't seem to be in game {game_id}."
            )

    @typing_command(name="table", aliases=["t"])
    async def table(self, ctx, game_id: Optional[int]):
        game_data = await self.get_game_data_from_ctx(ctx, game_id)
        if not game_data:
            return

        t = Texttable()
        t.set_deco(Texttable.HEADER)
        header = ["\nRound"]
        for p in game_data["player_stats"]:
            header.append(
                f"{p['username']}\n{plural(p['full_beers'], 'beer')}\n{plural(p['extra_sips'], 'sip')}"
            )

        t.header(header)

        cards = game_data["cards"]
        player_count = len(game_data["player_stats"])

        for i in range(13):
            row = [i + 1]
            for j in range(player_count):
                k = i * player_count + j
                row.append(cards[k]["value"] if k < len(cards) else "")

            t.add_row(row)

        await ctx.send(f"```\n{code_block_escape(t.draw())}\n```")

    @typing_command(name="eval")
    @commands.is_owner()
    async def eval(self, ctx, *, stmts):
        stmts = stmts.strip().strip("`")
        if not stmts:
            await ctx.send("After stripping `'s, stmts can't be empty.")
            return

        res = await eval_stmts(stmts, {"academy": self, "ctx": ctx})
        escaped = code_block_escape(repr(res))
        message = f"```python\n{escaped}\n```"
        if len(message) > MAX_DISCORD_MESSAGE_LENGTH:
            # The reason that we can safely truncate the message
            # is because of how code_block_escape works
            prefix = "Truncated result to length 0000:\n"
            suffix = "\n```"
            message = message.rstrip("`").strip()

            new_length = MAX_DISCORD_MESSAGE_LENGTH - len(prefix) - len(suffix)
            prefix = prefix.replace("0000", str(new_length))
            message = prefix + message[:new_length] + suffix

        await ctx.send(message)

    @typing_command(name="fura")
    async def fura(self, ctx, *, text):
        text = text.strip()

        img = Image.open(FURA_TEMPLATE)
        d = ImageDraw.Draw(img)
        fnt = get_max_font(d, "DejaVuSans.ttf", text, FURA_TEMPLATE_SIZE)
        size = d.textsize(text, fnt)
        offset = [
            template_offset + (template_size - text_size) // 2
            for text_size, template_size, template_offset in zip(
                size, FURA_TEMPLATE_SIZE, FURA_TEMPLATE_OFFSET
            )
        ]
        d.text(offset, text, font=fnt, fill=(0, 0, 0))

        with io.BytesIO() as f:
            img.save(f, format="png")
            f.seek(0)
            await ctx.send(file=File(f, "fura.png"))

    @typing_command(name="distribute", aliases=["d"])
    async def distribute(self, ctx, *players):
        TIMEOUT = 10

        n = len(players)

        groups = {}
        group_sizes = []
        for p in players:
            group = p.split("=")
            groups.setdefault(len(group), []).append(group)
            group_sizes.append(len(group))

        max_size = 6
        if max(group_sizes) > max_size:
            await ctx.send(f"Groups can't have size over {max_size}")
            return

        try:
            game_group_sizes = run_with_timeout(
                partition_solve, [group_sizes, 6], seconds=TIMEOUT,
            )
        except TimeoutError:
            await ctx.send(
                f"Timed out trying to find optimal solution after {TIMEOUT} seconds"
            )
            return

        n = len(game_group_sizes)

        for l in groups.values():
            random.shuffle(l)

        game_groups = []
        for group_sizes in game_group_sizes:
            game_group = []
            for k in group_sizes:
                game_group.append(groups[k].pop())

            game_groups.append(game_group)

        message = f"Partitioned players into {n} games:\n"
        for i, game_group in enumerate(game_groups):
            players = ", ".join([p for group in game_group for p in group])
            message += f"Game {i + 1}: {players}\n"

        await ctx.send(message)

    @typing_command(name="zoom", aliases=["z"])
    async def zoom(self, ctx):
        join_url = await zoom.generate_join_url(AU_ID, AU_PASSWORD)
        await ctx.send(f"Generated new zoom meeting: {join_url}")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.id == FURA_ID:
            await self.fura(message.channel, text=message.content)


bot.add_cog(Academy(bot))

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
