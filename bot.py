import asyncio
import pprint
import logging
import os

import aiohttp
from aiohttp.client_exceptions import ClientResponseError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from db import Link, State, session_scope
from discord import Game, utils
from discord.ext import commands
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)

load_dotenv()

GIT_COMMIT_HASH = os.environ["GIT_COMMIT_HASH"]
GIT_COMMIT_URL = f"https://github.com/beeracademy/discord-bot/commit/{GIT_COMMIT_HASH}"

bot = commands.Bot("!", case_insensitive=True)


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


class Academy(commands.Cog):
    TIMEOUT = aiohttp.ClientTimeout(total=5)

    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

        with session_scope() as session:
            try:
                state = session.query(State).one()
                self.current_game_id = state.followed_game
                self.game_data = self.bot.loop.run_until_complete(
                    self.get_game_data(self.current_game_id)
                )

                logging.info(f"Got game id from saved state: {self.current_game_id}.")
            except (NoResultFound, ClientResponseError):
                self.current_game_id = None
                self.game_data = None
                logging.info("No game id saved state.")

    async def update_status(self):
        async with self.lock:
            if self.current_game_id:
                await self.bot.change_presence(
                    activity=Game(
                        name=f"Giving updates on https://academy.beer/games/{self.current_game_id}/"
                    )
                )
            else:
                await self.bot.change_presence(
                    activity=Game(name="Waiting for new players: https://academy.beer/")
                )

    @commands.Cog.listener()
    async def on_ready(self):
        self.guild = utils.get(self.bot.guilds, name=os.environ["DISCORD_GUILD"])
        self.channel = utils.get(
            self.guild.channels, name=os.environ["DISCORD_CHANNEL"]
        )

        await self.update_status()
        logging.info(f"Connected as {self.bot.user}")
        self.bot.loop.create_task(self.background_task())

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

    async def send_info(self, game_data):
        player_stats = game_data["player_stats"]
        player_count = len(player_stats)
        card_count = len(game_data["cards"])
        player_index = card_count % player_count

        previous_player_name = self.get_player_name(
            player_stats[(player_index - 1) % len(player_stats)]
        )
        current_player_stats = player_stats[player_index]
        player_name = self.get_player_name(current_player_stats)

        message = ""
        if game_data["cards"]:
            card = game_data["cards"][-1]
            message += f"{previous_player_name} just got a {card['value']}.\n\n"

        message += f"Now it's {player_name}'s turn:\n" + self.level_info(
            player_stats[player_index]
        )

        await self.channel.send(message)

    async def post_game_update(self, game_data):
        player_count = len(game_data["player_stats"])
        total_card_count = player_count * 13
        if len(game_data["cards"]) == total_card_count:
            await self.channel.send(
                f"The game has finished, look at the stats at https://academy.beer/games/{self.current_game_id}/"
            )
        else:
            await self.send_info(game_data)

    async def get_game_data(self, game_id):
        async with aiohttp.ClientSession(
            raise_for_status=True, timeout=self.TIMEOUT
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
            raise_for_status=True, timeout=self.TIMEOUT
        ) as session:
            async with session.get(
                f"https://academy.beer/api/users/{user_id}/"
            ) as response:
                return (await response.json())["username"]

    async def background_task(self):
        while True:
            if self.current_game_id:
                try:
                    new_game_data = await self.get_game_data(self.current_game_id)
                except ClientResponseError:
                    await self.channel.send(
                        "Failed to get game data, unfollowing game."
                    )
                    await self.set_followed_game(None)

                if len(new_game_data["cards"]) != len(self.game_data["cards"]):
                    await self.post_game_update(new_game_data)

                self.game_data = new_game_data

            await asyncio.sleep(1)

    async def set_followed_game(self, game_id):
        self.current_game_id = game_id
        with session_scope() as session:
            session.query(State).delete()
            if self.current_game_id:
                session.add(State(followed_game=self.current_game_id))

        await self.update_status()

    @commands.command(name="follow")
    async def follow(self, ctx, game_id: int):
        try:
            self.game_data = await self.get_game_data(game_id)
        except ClientResponseError:
            await ctx.send("Couldn't get game data! Does the game exist?")
            return

        await self.set_followed_game(game_id)
        await ctx.send(f"Now following game {self.current_game_id}.")
        await self.post_game_update(self.game_data)

    @commands.command(name="unfollow")
    async def unfollow(self, ctx):
        game_id = self.current_game_id
        if game_id:
            await self.set_followed_game(None)
            self.game_data = None
            await ctx.send(f"Unfollowed game {game_id}.")
        else:
            await ctx.send(f"Not currently following any game!")

    def set_linked_account(self, discord_id, academy_id):
        with session_scope() as session:
            session.query(Link).filter(Link.discord_id == discord_id).delete()
            if academy_id:
                session.add(Link(discord_id=discord_id, academy_id=academy_id))

    @commands.command(name="link")
    async def link(self, ctx, academy_id: int):
        try:
            username = await self.get_username(academy_id)
        except:
            await ctx.send("Couldn't get user data! Does the user exist?")
            return

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

    @commands.command(name="unlink")
    async def unlink(self, ctx):
        self.set_linked_account(ctx.author.id, None)
        await ctx.send(
            f"{ctx.author.mention} is now no longer linked to any academy user."
        )

    @commands.command(name="test")
    async def test(self, ctx):
        await ctx.send(f"Test {ctx.author.mention}")

    @commands.command(name="version")
    async def version(self, ctx):
        await ctx.send(f"I'm currently running the following version: {GIT_COMMIT_URL}")

    @commands.command(name="status")
    async def status(self, ctx):
        await self.post_game_update(self.game_data)

    @commands.command(name="level")
    async def level(self, ctx):
        if not self.current_game_id:
            await ctx.send(
                f"{ctx.author.mention} the bot isn't currently following a game.\nTry using !follow"
            )
            return

        academy_id = self.get_academy_id(ctx.author.id)

        player_stats = get_dict(self.game_data["player_stats"], id=academy_id)
        if player_stats:
            s = f"{ctx.author.mention}:\n"
            s += self.level_info(player_stats)
            await ctx.send(s)
        else:
            await ctx.send(
                f"{ctx.author.mention} doesn't seem to be in game {self.current_game_id}.\nTry linking your accounts with !link"
            )

    @commands.command(name="debug")
    async def debug(self, ctx):
        await ctx.send(f"""Current game id: {self.current_game_id}
Current game data:
```python
{pprint.pformat(self.game_data)}
```""")


bot.add_cog(Academy(bot))
bot.run(os.environ["DISCORD_TOKEN"])
