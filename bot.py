import asyncio
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

bot = commands.Bot("!", case_insensitive=True)


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

                print(f"Got game id from saved state: {self.current_game_id}.")
            except (NoResultFound, ClientResponseError):
                self.current_game_id = None
                print("No game id saved state.")

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
        self.channel = utils.get(self.guild.channels, name=os.environ["DISCORD_CHANNEL"])

        await self.update_status()
        print(f"Connected as {self.bot.user}")
        self.bot.loop.create_task(self.background_task())

    def get_player_name(self, player_stats):
        academy_id = player_stats["id"]
        with session_scope() as session:
            try:
                link = session.query(Link).filter(Link.academy_id == academy_id).one()
                discord_id = link.discord_id
                return self.bot.get_user(discord_id).mention
            except NoResultFound:
                return player_stats["username"]

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

        message += f"""Now it's {player_name}'s turn:
To be on level they have to have drunk {plural(current_player_stats['full_beers'], 'beer')} and {plural(current_player_stats['extra_sips'], 'sip')}."""

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
            async with session.get(
                f"https://academy.beer/api/games/{game_id}/"
            ) as response:
                res = await response.json()
                return res

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
            await ctx.send(f"Unfollowed game {game_id}.")
        else:
            await ctx.send(f"Not currently following any game!")

    @commands.command(name="link")
    async def link(self, ctx, academy_id: int):
        try:
            username = await self.get_username(academy_id)
        except:
            await ctx.send("Couldn't get user data! Does the user exist?")
            return

        discord_id = ctx.author.id

        with session_scope() as session:
            session.query(Link).filter(Link.discord_id == discord_id).delete()
            try:
                session.add(Link(discord_id=discord_id, academy_id=academy_id))
            except IntegrityError:
                await ctx.send(
                    f"{ctx.author.mention} that academy user is already linked to someone else!"
                )
                return

        await ctx.send(
            f"{ctx.author.mention} is now linked with {username} on academy."
        )

    @commands.command(name="test")
    async def test(self, ctx):
        await ctx.send(f"Test {ctx.author.mention}")

    @commands.command(name="status")
    async def status(self, ctx):
        await self.post_game_update(self.game_data)


bot.add_cog(Academy(bot))
bot.run(os.environ["DISCORD_TOKEN"])
