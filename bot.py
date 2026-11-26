import asyncio
import os
import random
from typing import Optional

from aiohttp import web
import discord
from discord.ext import commands
from dotenv import load_dotenv

import database

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DEFAULT_BET = 50
ANIMATION_DELAY = 0.6

# Symbole bębnów wraz z wagami losowania i mnożnikami za 3 takie same symbole
SLOT_CONFIG = [
    {"symbol": "🍒", "name": "Wiśnie", "weight": 35, "multiplier_3": 3},
    {"symbol": "🍋", "name": "Cytryna", "weight": 28, "multiplier_3": 4},
    {"symbol": "🍇", "name": "Winogrona", "weight": 18, "multiplier_3": 6},
    {"symbol": "🔔", "name": "Dzwonek", "weight": 12, "multiplier_3": 10},
    {"symbol": "💎", "name": "Diament", "weight": 5, "multiplier_3": 25},
    {"symbol": "7️⃣", "name": "Siódemka", "weight": 2, "multiplier_3": 50},
]

SYMBOLS = [item["symbol"] for item in SLOT_CONFIG]
WEIGHTS = [item["weight"] for item in SLOT_CONFIG]
MULTIPLIERS_3 = {item["symbol"]: item["multiplier_3"] for item in SLOT_CONFIG}

# Zabezpieczenie przed równoczesnym kręceniem przez tego samego gracza
ACTIVE_SPINS: set[tuple[int, int]] = set()


def get_guild_id(guild: Optional[discord.Guild]) -> int:
    """Zwraca ID serwera lub 0 dla wiadomości prywatnych."""
    return guild.id if guild is not None else 0


def format_duration(seconds: int) -> str:
    """Formatuje liczbę sekund na czytelny zapis godzin, minut i sekund."""
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours > 0:
        parts.append(f"{hours} godz.")
    if minutes > 0:
        parts.append(f"{minutes} min.")
    if secs > 0 or not parts:
        parts.append(f"{secs} sek.")
    return " ".join(parts)


def spin_reels() -> list[str]:
    """Losuje 3 symbole z uwzględnieniem wag."""
    return random.choices(SYMBOLS, weights=WEIGHTS, k=3)


def calculate_payout(reels: list[str], bet: int) -> tuple[int, str]:
    """
    Oblicza wygraną dla wylosowanych 3 symboli.
    Zwraca (kwota_wygranej, opis_wyniku).
    """
    unique_count = len(set(reels))
    if unique_count == 1:
        symbol = reels[0]
        multiplier = MULTIPLIERS_3[symbol]
        winnings = bet * multiplier
        return winnings, f"JACKPOT! 3x {symbol} (mnożnik **x{multiplier}**)"
    if unique_count == 2:
        winnings = int(bet * 1.5)
        return winnings, "Dwa takie same symbole! (zwrot **x1.5**)"
    return 0, "Brak trafienia. Spróbuj ponownie!"


def generate_spin_frames(final_reels: list[str]) -> list[list[str]]:
    """Generuje 3 klatki animacji bębnów przed wyświetleniem wyniku."""
    return [
        spin_reels(),
        [final_reels[0], random.choice(SYMBOLS), random.choice(SYMBOLS)],
        [final_reels[0], final_reels[1], random.choice(SYMBOLS)],
    ]


def resolve_spin_result(
    guild_id: int,
    user_id: int,
    bet: int,
    final_reels: list[str],
    current_balance: int,
) -> tuple[int, str, int]:
    """Oblicza wygraną i aktualizuje saldo w bazie danych."""
    winnings, result_text = calculate_payout(final_reels, bet)
    new_balance = (
        database.add_coins(guild_id, user_id, winnings)
        if winnings > 0
        else current_balance
    )
    return winnings, result_text, new_balance


def build_spin_frame_embed(
    user: discord.abc.User,
    bet: int,
    reels: list[str],
    frame_number: int,
) -> discord.Embed:
    """Tworzy Embed dla pojedynczej klatki animacji automatu."""
    embed = discord.Embed(
        title="🎰 Automat Kasynowy — Kręcenie...",
        description=(
            f"**Gracz:** {user.mention}\n"
            f"**Stawka:** `{bet}` monet\n\n"
            f"┏━━━━━━━━━━━┓\n"
            f"┃  {reels[0]}  │  {reels[1]}  │  {reels[2]}  ┃\n"
            f"┗━━━━━━━━━━━┛"
        ),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"Klatka {frame_number}/3 • Trwa losowanie...")
    return embed


def build_result_embed(
    user: discord.abc.User,
    bet: int,
    reels: list[str],
    winnings: int,
    result_text: str,
    new_balance: int,
) -> discord.Embed:
    """Tworzy końcowy Embed z wynikiem losowania i zaktualizowanym saldem."""
    if winnings > bet:
        color = discord.Color.green()
        title = "🎉 Wygrana w Automacie!"
    elif winnings > 0:
        color = discord.Color.teal()
        title = "✨ Zwrot stawki w Automacie!"
    else:
        color = discord.Color.red()
        title = "💸 Przegrana w Automacie"

    net = winnings - bet
    net_str = f"+{net}" if net > 0 else str(net)

    embed = discord.Embed(
        title=title,
        description=(
            f"**Gracz:** {user.mention}\n"
            f"**Stawka:** `{bet}` monet\n\n"
            f"┏━━━━━━━━━━━┓\n"
            f"┃  {reels[0]}  │  {reels[1]}  │  {reels[2]}  ┃\n"
            f"┗━━━━━━━━━━━┛\n\n"
            f"**Wynik:** {result_text}\n"
            f"**Wygrana:** `{winnings}` monet (`{net_str}`)\n"
            f"**Aktualne saldo:** `{new_balance}` monet"
        ),
        color=color,
    )
    embed.set_footer(text="Kliknij przycisk poniżej, aby zakręcić ponownie za tę samą stawkę.")
    return embed


class SlotView(discord.ui.View):
    """Interaktywny widok z przyciskiem ponownego kręcenia dla autora komendy."""

    def __init__(self, author_id: int, guild_id: int, bet: int) -> None:
        super().__init__(timeout=180)
        self.author_id = author_id
        self.guild_id = guild_id
        self.bet = bet
        self.message: Optional[discord.Message] = None

    @discord.ui.button(label="🎰 Zakręć ponownie", style=discord.ButtonStyle.primary)
    async def spin_again(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Ten przycisk jest przypisany wyłącznie do autora komendy!",
                ephemeral=True,
            )
            return

        spin_key = (self.guild_id, self.author_id)
        if spin_key in ACTIVE_SPINS:
            await interaction.response.send_message(
                "⏳ Poczekaj, aż Twoje obecne kręcenie się zakończy!",
                ephemeral=True,
            )
            return

        ok, current_balance = database.try_deduct_bet(
            self.guild_id, self.author_id, self.bet
        )
        if not ok:
            await interaction.response.send_message(
                f"❌ Nie masz wystarczającej liczby monet na stawkę `{self.bet}`.\n"
                f"Twoje obecne saldo: `{current_balance}` monet.",
                ephemeral=True,
            )
            return

        ACTIVE_SPINS.add(spin_key)
        button.disabled = True
        try:
            final_reels = spin_reels()
            frames = generate_spin_frames(final_reels)

            await interaction.response.edit_message(
                embed=build_spin_frame_embed(interaction.user, self.bet, frames[0], 1),
                view=self,
            )
            await asyncio.sleep(ANIMATION_DELAY)

            for idx, frame_reels in enumerate(frames[1:], start=2):
                await interaction.edit_original_response(
                    embed=build_spin_frame_embed(interaction.user, self.bet, frame_reels, idx),
                    view=self,
                )
                await asyncio.sleep(ANIMATION_DELAY)

            winnings, result_text, new_balance = resolve_spin_result(
                self.guild_id, self.author_id, self.bet, final_reels, current_balance
            )

            button.disabled = False
            result_embed = build_result_embed(
                interaction.user,
                self.bet,
                final_reels,
                winnings,
                result_text,
                new_balance,
            )
            await interaction.edit_original_response(embed=result_embed, view=self)
        finally:
            ACTIVE_SPINS.discard(spin_key)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class CasinoBot(commands.Bot):
    """Główna klasa bota kasynowego."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self._web_runner: Optional[web.AppRunner] = None

    async def _handle_root(self, request: web.Request) -> web.Response:
        return web.Response(text="Bot dziala")

    async def start_web_server(self) -> None:
        """Uruchamia lekki serwer HTTP w tle na potrzeby healthchecka."""
        app = web.Application()
        app.router.add_get("/", self._handle_root)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 8080))
        site = web.TCPSite(runner, host="0.0.0.0", port=port)
        await site.start()
        self._web_runner = runner
        print(f"Serwer WWW nasłuchuje na porcie {port}.")

    async def setup_hook(self) -> None:
        database.init_db()
        await self.start_web_server()

    async def close(self) -> None:
        if self._web_runner is not None:
            await self._web_runner.cleanup()
            self._web_runner = None
        await super().close()


bot = CasinoBot()


@bot.event
async def on_ready() -> None:
    database.init_db()
    print(f"Zalogowano jako {bot.user} (ID: {bot.user.id})")
    print("Baza danych kasyno.db jest gotowa.")


@bot.command(name="slot", help="Zakręć automatem za określoną stawkę (domyślnie 50).")
async def slot(ctx: commands.Context, stawka: int = DEFAULT_BET) -> None:
    if stawka <= 0:
        await ctx.send("❌ Stawka musi być większa od zera!")
        return

    guild_id = get_guild_id(ctx.guild)
    user_id = ctx.author.id
    spin_key = (guild_id, user_id)

    if spin_key in ACTIVE_SPINS:
        await ctx.send("⏳ Poczekaj, aż Twoje obecne kręcenie się zakończy!")
        return

    ok, current_balance = database.try_deduct_bet(guild_id, user_id, stawka)
    if not ok:
        embed = discord.Embed(
            title="❌ Za mało monet!",
            description=(
                f"{ctx.author.mention}, nie masz wystarczającej liczby monet na grę za `{stawka}`.\n"
                f"**Twoje saldo:** `{current_balance}` monet.\n"
                f"Użyj `!daily`, aby odebrać darmowe `500` monet raz na 24 godziny."
            ),
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)
        return

    ACTIVE_SPINS.add(spin_key)
    try:
        final_reels = spin_reels()
        frames = generate_spin_frames(final_reels)

        msg = await ctx.send(
            embed=build_spin_frame_embed(ctx.author, stawka, frames[0], 1)
        )
        await asyncio.sleep(ANIMATION_DELAY)

        for idx, frame_reels in enumerate(frames[1:], start=2):
            await msg.edit(
                embed=build_spin_frame_embed(ctx.author, stawka, frame_reels, idx)
            )
            await asyncio.sleep(ANIMATION_DELAY)

        winnings, result_text, new_balance = resolve_spin_result(
            guild_id, user_id, stawka, final_reels, current_balance
        )

        view = SlotView(author_id=user_id, guild_id=guild_id, bet=stawka)
        result_embed = build_result_embed(
            ctx.author, stawka, final_reels, winnings, result_text, new_balance
        )
        await msg.edit(embed=result_embed, view=view)
        view.message = msg
    finally:
        ACTIVE_SPINS.discard(spin_key)


@bot.command(name="saldo", help="Sprawdza aktualny stan konta (Twojego lub oznaczonego gracza).")
async def saldo(ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
    target = member or ctx.author
    guild_id = get_guild_id(ctx.guild)
    balance = database.get_balance(guild_id, target.id)

    embed = discord.Embed(
        title="💰 Stan Konta",
        description=f"**Gracz:** {target.mention}\n**Saldo:** `{balance}` monet",
        color=discord.Color.blurple(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="daily", help="Odbiera darmowe 500 monet raz na 24 godziny.")
async def daily(ctx: commands.Context) -> None:
    guild_id = get_guild_id(ctx.guild)
    claimed, balance, remaining = database.claim_daily(guild_id, ctx.author.id)

    if claimed:
        embed = discord.Embed(
            title="🎁 Dzienna Nagroda Odebrana!",
            description=(
                f"{ctx.author.mention}, otrzymałeś `{database.DAILY_REWARD}` darmowych monet!\n"
                f"**Aktualne saldo:** `{balance}` monet"
            ),
            color=discord.Color.green(),
        )
    else:
        embed = discord.Embed(
            title="⏳ Nagroda już odebrana",
            description=(
                f"{ctx.author.mention}, dzienną nagrodę możesz odebrać raz na 24 godziny.\n"
                f"**Pozostały czas:** `{format_duration(remaining)}`\n"
                f"**Aktualne saldo:** `{balance}` monet"
            ),
            color=discord.Color.orange(),
        )
    await ctx.send(embed=embed)


@bot.command(name="top", help="Wyświetla ranking 10 najbogatszych graczy na serwerze.")
async def top(ctx: commands.Context) -> None:
    guild_id = get_guild_id(ctx.guild)
    top_players = database.get_top_players(guild_id, limit=10)

    if not top_players:
        await ctx.send("📉 Brak graczy w rankingu na tym serwerze. Zagraj w `!slot` lub odbierz `!daily`!")
        return

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = []

    for index, (uid, balance) in enumerate(top_players, start=1):
        prefix = medals.get(index, f"**{index}.**")
        display_name = None

        if ctx.guild is not None:
            member = ctx.guild.get_member(uid)
            if member is not None:
                display_name = member.display_name

        if display_name is None:
            user = bot.get_user(uid)
            if user is not None:
                display_name = user.display_name

        if display_name is None:
            try:
                user = await bot.fetch_user(uid)
                display_name = user.display_name
            except (discord.NotFound, discord.HTTPException):
                display_name = f"Gracz ({uid})"

        lines.append(f"{prefix} **{discord.utils.escape_markdown(display_name)}** — `{balance}` monet")

    server_name = ctx.guild.name if ctx.guild is not None else "Wiadomości prywatne"
    embed = discord.Embed(
        title=f"🏆 Top 10 Najbogatszych Graczy — {server_name}",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    await ctx.send(embed=embed)


@slot.error
async def slot_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.BadArgument):
        await ctx.send("❌ Podaj poprawną liczbę całkowitą jako stawkę, np. `!slot 100`.")
        return
    raise error


@saldo.error
async def saldo_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Nie znaleziono podanego użytkownika na serwerze.")
        return
    raise error


def main() -> None:
    database.init_db()
    if not TOKEN or TOKEN == "TUTAJ_WKLEJ_SWOJ_TOKEN_BOTA":
        raise RuntimeError(
            "Brak poprawnego DISCORD_TOKEN w pliku .env. "
            "Uzupełnij zmienną DISCORD_TOKEN swoim tokenem bota z Discord Developer Portal."
        )
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
