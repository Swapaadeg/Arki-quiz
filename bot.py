import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import os
import json
import random
import asyncio
from datetime import datetime
from contextlib import suppress
from typing import Dict, List, Optional, Set, Tuple
from urllib import error as urllib_error
from urllib import request as urllib_request

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

QUESTIONS_FILE = os.path.join(os.path.dirname(__file__), "questions.json")
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "quiz_history.json")
QUIZ_CHANNEL_ID = os.getenv("QUIZ_CHANNEL_ID")
QUIZ_GUILD_ID = os.getenv("QUIZ_GUILD_ID")
INVENTORY_API_URL = os.getenv("INVENTORY_API_URL")
INVENTORY_API_KEY = os.getenv("INVENTORY_API_KEY") or os.getenv("EXTERNAL_API_KEY")
INVENTORY_API_AUTH_HEADER = os.getenv("INVENTORY_API_AUTH_HEADER", "X-API-Key")
INVENTORY_CURRENCY_NAME = os.getenv("INVENTORY_CURRENCY_NAME", "fraise")
INVENTORY_API_TIMEOUT_SECONDS = int(os.getenv("INVENTORY_API_TIMEOUT_SECONDS", "10"))

def load_questions():
    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        return json.load(f)

categories = load_questions()
CATEGORY_CHOICES = [
    app_commands.Choice(name=name, value=name)
    for name in list(categories.keys())[:25]
]

# Scores en mémoire (réinitialisés quand le bot redémarre)
scores = {}
active_quizzes = {}

QUESTION_TIME_LIMIT = 15
QUESTION_POINTS = 500
NEXT_QUESTION_DELAY = 10
DEFAULT_QUESTION_COUNT = 20
DEFAULT_ANNOUNCE_DELAY_MINUTES = 5
QUIZ_LAUNCHER_ROLE_NAME = os.getenv("QUIZ_LAUNCHER_ROLE_NAME", "quiz-launcher")
POINTS_EMOJI = "🍓"


def can_manage_quiz(member: discord.abc.User, guild: Optional[discord.Guild]) -> bool:
    if guild is None:
        return False

    if isinstance(member, discord.Member):
        perms = member.guild_permissions
        if perms.administrator or perms.manage_guild:
            return True

    return member.id == guild.owner_id


def has_quiz_launcher_role(member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False

    return any(role.name.lower() == QUIZ_LAUNCHER_ROLE_NAME.lower() for role in member.roles)


def is_ignorable_interaction_error(error: Exception) -> bool:
    current: Optional[Exception] = error
    while current is not None:
        if isinstance(current, discord.NotFound) and current.code == 10062:
            return True
        if isinstance(current, discord.HTTPException) and current.code == 40060:
            return True
        original = getattr(current, "original", None)
        if isinstance(original, Exception):
            current = original
            continue
        break
    return False


async def send_interaction_message(
    interaction: discord.Interaction,
    content: str,
    *,
    ephemeral: bool = True,
) -> bool:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)
        return True
    except Exception as error:
        if is_ignorable_interaction_error(error):
            return False
        raise


def compute_score(elapsed_seconds: float) -> int:
    remaining = max(0.0, QUESTION_TIME_LIMIT - elapsed_seconds)
    ratio = remaining / QUESTION_TIME_LIMIT
    return int(round(QUESTION_POINTS * ratio))


def format_choices(choices: List[str]) -> str:
    return "\n".join(f"**{i + 1}.** {choice}" for i, choice in enumerate(choices))


def format_announce_time(total_seconds: int) -> str:
    if total_seconds <= 60:
        return f"{total_seconds}s"
    minutes = total_seconds // 60
    return f"{minutes} min"


def load_quiz_history() -> List[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Erreur chargement historique: {e}")
        return []


def save_quiz_to_history(categorie: str, session_scores: Dict[int, int]) -> None:
    if not session_scores:
        return
    
    history = load_quiz_history()
    
    quiz_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "categorie": categorie,
        "participants": [
            {
                "user_id": user_id,
                "points": points,
            }
            for user_id, points in sorted(
                session_scores.items(), key=lambda x: x[1], reverse=True
            )
        ],
    }
    
    history.append(quiz_entry)
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Erreur sauvegarde historique: {e}")


def _post_inventory_reward(user_id: int, amount: int) -> Tuple[bool, str]:
    if not INVENTORY_API_URL:
        return False, "INVENTORY_API_URL non configuree"

    payload = json.dumps(
        {
            "user_id": str(user_id),
            "discord_user_id": str(user_id),
            "amount": amount,
            "points": amount,
            "currency": INVENTORY_CURRENCY_NAME,
            "source": "arki-quizz",
        }
    ).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if INVENTORY_API_KEY:
        headers[INVENTORY_API_AUTH_HEADER] = INVENTORY_API_KEY

    request = urllib_request.Request(
        INVENTORY_API_URL,
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib_request.urlopen(request, timeout=INVENTORY_API_TIMEOUT_SECONDS) as response:
            status_code = response.getcode()
            if 200 <= status_code < 300:
                return True, f"HTTP {status_code}"
            return False, f"HTTP {status_code}"
    except urllib_error.HTTPError as http_error:
        details = ""
        try:
            details = http_error.read().decode("utf-8")
        except Exception:
            details = ""
        if details:
            return False, f"HTTP {http_error.code} {details[:200]}"
        return False, f"HTTP {http_error.code}"
    except Exception as exc:
        return False, str(exc)


async def sync_inventory_rewards(session_scores: Dict[int, int]) -> Tuple[int, int]:
    if not session_scores:
        return 0, 0

    tasks = [
        asyncio.to_thread(_post_inventory_reward, user_id, points)
        for user_id, points in session_scores.items()
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success_count = 0
    failure_count = 0
    for (user_id, _), result in zip(session_scores.items(), results):
        if isinstance(result, Exception):
            failure_count += 1
            print(f"[inventory-sync] user={user_id} error={result}")
            continue

        ok, detail = result
        if ok:
            success_count += 1
        else:
            failure_count += 1
            print(f"[inventory-sync] user={user_id} failed={detail}")

    return success_count, failure_count


class QuizSession:
    def __init__(self, channel_id: int):
        self.channel_id = channel_id
        self.paused = False
        self.stopped = False
        self.current_view: Optional[QuizView] = None

    def toggle_pause(self) -> bool:
        self.paused = not self.paused
        if self.current_view:
            if self.paused:
                self.current_view.pause()
            else:
                self.current_view.resume()
        return self.paused

    def stop(self):
        self.stopped = True
        if self.current_view:
            self.current_view.resume()


async def run_announcement_countdown(
    channel: discord.abc.Messageable,
    categorie: str,
    delay_seconds: int,
    session: QuizSession,
):
    if delay_seconds <= 0:
        return

    def build_content(remaining: int) -> str:
        pause_suffix = " Pause." if session.paused else ""
        return (
            f"@everyone Le arki-quiz commence dans {format_announce_time(remaining)}. "
            f"Le theme du jour est \"{categorie}\".{pause_suffix}"
        )

    def build_final_countdown(remaining: int) -> str:
        pause_suffix = " ⏸️ PAUSE" if session.paused else ""
        timer_display = "🔴" * remaining if remaining <= 10 else f"⏱️ {remaining}s"
        return (
            f"⚠️ **ATTENTION DEBUT DANS {remaining}s** ⚠️\n"
            f"{timer_display}\n\n"
            f"Thème: **{categorie.upper()}**{pause_suffix}"
        )

    try:
        message = await channel.send(build_content(delay_seconds))
    except discord.HTTPException:
        return

    loop = asyncio.get_event_loop()
    start_time = loop.time()
    total_paused = 0.0
    pause_start: Optional[float] = None
    last_remaining = delay_seconds
    final_message = None

    try:
        while not session.stopped:
            now = loop.time()

            if session.paused:
                if pause_start is None:
                    pause_start = now
                remaining = last_remaining
            else:
                if pause_start is not None:
                    total_paused += now - pause_start
                    pause_start = None
                elapsed = (now - start_time) - total_paused
                remaining = max(0, delay_seconds - int(elapsed))
                last_remaining = remaining

            if remaining <= 0:
                break

            await message.edit(content=build_content(remaining))

            if remaining <= 60 and final_message is None:
                try:
                    final_message = await channel.send(build_final_countdown(remaining))
                except discord.HTTPException:
                    final_message = None
            elif final_message and remaining <= 60:
                try:
                    await final_message.edit(content=build_final_countdown(remaining))
                except (discord.NotFound, discord.HTTPException):
                    final_message = None

            await asyncio.sleep(1)
    except (discord.NotFound, discord.HTTPException):
        return




async def update_countdown(
    message: discord.Message,
    view: "QuizView",
    base_footer: str,
):
    try:
        while not view.revealed:
            remaining = view.get_remaining_seconds()
            if message.embeds:
                embed = message.embeds[0]
                pause_suffix = " Pause." if view.is_paused else ""
                embed.set_footer(text=f"{base_footer} Temps: {remaining}s.{pause_suffix}")
                await message.edit(embed=embed, view=view)

            if remaining <= 0 and not view.is_paused:
                if not view.revealed:
                    await view.reveal_answer()
                    view.stop()
                break

            await asyncio.sleep(1)
    except (discord.NotFound, discord.HTTPException):
        return


async def run_quiz(
    channel: discord.abc.Messageable,
    categorie: str,
    question_count: int,
    announce_delay_seconds: int,
):
    if channel.id in active_quizzes:
        return "Un quizz est deja en cours dans ce salon."

    session = QuizSession(channel.id)
    active_quizzes[channel.id] = session

    await run_announcement_countdown(
        channel, categorie, announce_delay_seconds, session
    )
    if session.stopped:
        active_quizzes.pop(channel.id, None)
        return None

    questions = categories[categorie]
    selected_questions = random.sample(questions, k=question_count)

    session_scores: Dict[int, int] = {}
    participant_ids: Set[int] = set()

    try:
        for index, q in enumerate(selected_questions, start=1):
            if session.stopped:
                break
            base_footer = (
                "Clique sur 1/2/3/4 — 15 secondes. "
                f"Les {POINTS_EMOJI} dependent du temps."
            )
            embed = discord.Embed(
                title=f"Quizz — {categorie.upper()} ({index}/{question_count})",
                description=f"{q['question']}\n\n{format_choices(q['choices'])}",
                color=discord.Color.blue(),
            )
            embed.set_footer(text=f"{base_footer} Temps: {QUESTION_TIME_LIMIT}s.")

            view = QuizView(
                choices=q["choices"],
                correct_index=q["answer"],
                start_time=discord.utils.utcnow(),
                session_scores=session_scores,
                participant_ids=participant_ids,
            )
            message = await channel.send(embed=embed, view=view)
            view.message = message
            session.current_view = view
            if session.paused:
                view.pause()

            countdown_task = asyncio.create_task(
                update_countdown(message, view, base_footer)
            )

            await view.wait()
            await view.reveal_answer()
            countdown_task.cancel()
            with suppress(asyncio.CancelledError):
                await countdown_task
            session.current_view = None
            await asyncio.sleep(NEXT_QUESTION_DELAY)

        if session.stopped:
            return None

        if not participant_ids:
            await channel.send("Quizz termine. Personne n'a participe.")
            return None

        sorted_scores = sorted(
            session_scores.items(), key=lambda x: x[1], reverse=True
        )
        leaderboard = []
        for i, (user_id, pts) in enumerate(sorted_scores, start=1):
            user = await bot.fetch_user(user_id)
            leaderboard.append(f"**{i}.** {user.name} — {pts} {POINTS_EMOJI}")

        embed = discord.Embed(
            title="Classement du quizz",
            description="\n".join(leaderboard),
            color=discord.Color.gold(),
        )
        await channel.send(embed=embed)

        save_quiz_to_history(categorie, session_scores)

        success_count, failure_count = await sync_inventory_rewards(session_scores)
        if failure_count == 0 and success_count > 0:
            await channel.send(
                f"Inventaire synchronise pour **{success_count}** joueur(s)."
            )
        elif failure_count > 0:
            await channel.send(
                "Synchronisation inventaire partielle ou echouee. "
                f"Succes: **{success_count}**, echecs: **{failure_count}**."
            )
    finally:
        active_quizzes.pop(channel.id, None)

    return None


class AnswerButton(discord.ui.Button):
    def __init__(self, index: int):
        super().__init__(label=str(index + 1), style=discord.ButtonStyle.secondary)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: "QuizView" = self.view
        if view is None:
            return
        await view.register_answer(interaction, self.index)


class QuizView(discord.ui.View):
    def __init__(
        self,
        *,
        choices: List[str],
        correct_index: int,
        start_time: datetime,
        session_scores: Dict[int, int],
        participant_ids: Set[int],
    ):
        super().__init__(timeout=QUESTION_TIME_LIMIT)
        self.choices = choices
        self.correct_index = correct_index
        self.start_time = start_time
        self.session_scores = session_scores
        self.participant_ids = participant_ids
        self.message: Optional[discord.Message] = None
        self.user_answers: Dict[int, Dict[str, float | int]] = {}
        self.revealed = False
        self.pause_started_at: Optional[datetime] = None
        self.total_paused_seconds = 0.0

        for i in range(len(choices)):
            self.add_item(AnswerButton(i))

    async def register_answer(self, interaction: discord.Interaction, index: int):
        if self.is_paused:
            await interaction.response.send_message(
                "Le quizz est en pause.", ephemeral=True
            )
            return
        self.participant_ids.add(interaction.user.id)
        self.session_scores.setdefault(interaction.user.id, 0)
        scores.setdefault(interaction.user.id, 0)
        elapsed = self.get_elapsed_seconds()
        self.user_answers[interaction.user.id] = {
            "index": index,
            "elapsed": elapsed,
        }

        await interaction.response.send_message(
            f"Reponse {index + 1} enregistree.", ephemeral=True
        )

    async def reveal_answer(self):
        if self.revealed:
            return
        self.revealed = True
        for user_id in self.participant_ids:
            self.session_scores.setdefault(user_id, 0)
            scores.setdefault(user_id, 0)
        for user_id, payload in self.user_answers.items():
            if int(payload["index"]) == self.correct_index:
                points = compute_score(float(payload["elapsed"]))
                self.session_scores[user_id] = (
                    self.session_scores.get(user_id, 0) + points
                )
                scores[user_id] = scores.get(user_id, 0) + points
        for child in self.children:
            if not isinstance(child, AnswerButton):
                continue
            child.disabled = True
            if child.index == self.correct_index:
                child.style = discord.ButtonStyle.success
            else:
                child.style = discord.ButtonStyle.danger

        if self.message:
            await self.message.edit(view=self)

    async def on_timeout(self):
        await self.reveal_answer()

    @property
    def is_paused(self) -> bool:
        return self.pause_started_at is not None

    def pause(self):
        if self.pause_started_at is None:
            self.pause_started_at = discord.utils.utcnow()

    def resume(self):
        if self.pause_started_at is not None:
            self.total_paused_seconds += (
                discord.utils.utcnow() - self.pause_started_at
            ).total_seconds()
            self.pause_started_at = None

    def get_elapsed_seconds(self) -> float:
        reference = self.pause_started_at or discord.utils.utcnow()
        elapsed = (reference - self.start_time).total_seconds()
        elapsed -= self.total_paused_seconds
        return max(0.0, elapsed)

    def get_remaining_seconds(self) -> int:
        elapsed = self.get_elapsed_seconds()
        return max(0, int(QUESTION_TIME_LIMIT - elapsed))


@bot.event
async def on_ready():
    print(f"Bot connecté en tant que {bot.user}")
    if not hasattr(bot, "synced"):
        if QUIZ_GUILD_ID:
            try:
                guild_id = int(QUIZ_GUILD_ID)
            except ValueError:
                print("Config invalide: QUIZ_GUILD_ID.")
                await bot.tree.sync()
            else:
                guild = discord.Object(id=guild_id)
                bot.tree.copy_global_to(guild=guild)
                bot.tree.clear_commands(guild=None)
                await bot.tree.sync()
                await bot.tree.sync(guild=guild)
        else:
            await bot.tree.sync()
        bot.synced = True


@bot.tree.command(name="quiz", description="Lance un quizz")
@app_commands.describe(
    categorie="Categorie",
    nombre="Nombre de questions",
    delai="Delai avant debut (en minutes)",
)
@app_commands.choices(categorie=CATEGORY_CHOICES)
async def quiz_slash(
    interaction: discord.Interaction,
    categorie: str,
    nombre: Optional[int] = DEFAULT_QUESTION_COUNT,
    delai: Optional[int] = DEFAULT_ANNOUNCE_DELAY_MINUTES,
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Le quizz doit etre lance dans un serveur.", ephemeral=True
        )
        return

    try:
        await interaction.response.defer(ephemeral=True)
    except discord.InteractionResponded:
        pass
    except (discord.NotFound, discord.HTTPException):
        return

    if not can_manage_quiz(interaction.user, interaction.guild):
        await interaction.followup.send(
            "Tu dois etre administrateur ou avoir 'Gerer le serveur' pour lancer /quiz.", ephemeral=True
        )
        return

    if QUIZ_CHANNEL_ID:
        try:
            allowed_channel_id = int(QUIZ_CHANNEL_ID)
        except ValueError:
            await interaction.followup.send(
                "Config invalide: QUIZ_CHANNEL_ID.", ephemeral=True
            )
            return

        if interaction.channel_id != allowed_channel_id:
            await interaction.followup.send(
                "Ce quizz doit etre lance dans le salon dedie.", ephemeral=True
            )
            return

    categorie = categorie.lower()
    if categorie not in categories:
        await interaction.followup.send(
            "Categorie inconnue.", ephemeral=True
        )
        return

    if nombre is None or nombre < 1:
        await interaction.followup.send(
            "Le nombre de questions doit etre au moins 1.", ephemeral=True
        )
        return

    if delai is None or delai < 0:
        await interaction.followup.send(
            "Le delai doit etre 0 ou un entier positif.", ephemeral=True
        )
        return

    question_count = min(nombre, len(categories[categorie]))
    await interaction.followup.send(
        f"Quizz lance dans {interaction.channel.mention}.", ephemeral=True
    )

    announce_delay_seconds = (delai or 0) * 60
    error = await run_quiz(
        interaction.channel,
        categorie,
        question_count,
        announce_delay_seconds,
    )
    if error:
        await interaction.followup.send(error, ephemeral=True)


@bot.command()
async def quiz(
    ctx,
    categorie: str = None,
    nombre: Optional[str] = None,
    delai: Optional[str] = None,
):
    """Lance un quizz. Utilise : !quiz <categorie> [nombre] [delai]"""
    if not ctx.guild:
        await ctx.send("Le quizz doit etre lance dans un serveur.")
        return

    if not can_manage_quiz(ctx.author, ctx.guild):
        await ctx.send("Tu dois etre proprietaire du serveur, admin, ou avoir 'Gerer le serveur' pour lancer le quizz.")
        return

    if QUIZ_CHANNEL_ID:
        try:
            allowed_channel_id = int(QUIZ_CHANNEL_ID)
        except ValueError:
            await ctx.send("Config invalide: QUIZ_CHANNEL_ID.")
            return

        if ctx.channel.id != allowed_channel_id:
            await ctx.send("Ce quizz doit etre lance dans le salon dedie.")
            return

    if categorie is None:
        listing = "\n".join(
            f"**·** `{name}` ({len(qs)} questions)" for name, qs in categories.items()
        )
        embed = discord.Embed(
            title="Categories disponibles",
            description=f"{listing}\n\nUtilise `!quiz <categorie> [nombre]` pour jouer.",
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed)
        return

    categorie = categorie.lower()
    if categorie not in categories:
        await ctx.send("Categorie inconnue. Tape `!quiz` pour voir la liste.")
        return

    questions = categories[categorie]
    if nombre is None:
        question_count = min(DEFAULT_QUESTION_COUNT, len(questions))
    else:
        if not nombre.isdigit():
            await ctx.send(
                "Le nombre de questions doit etre un entier. Exemple: `!quiz ark 5 3`."
            )
            return
        question_count = min(int(nombre), len(questions))
        if question_count < 1:
            await ctx.send("Le nombre de questions doit etre au moins 1.")
            return

    if delai is None:
        announce_delay_minutes = DEFAULT_ANNOUNCE_DELAY_MINUTES
    else:
        if not delai.isdigit():
            await ctx.send(
                "Le delai doit etre un entier en minutes. Exemple: `!quiz ark 5 3`."
            )
            return
        announce_delay_minutes = int(delai)
        if announce_delay_minutes < 0:
            await ctx.send("Le delai doit etre 0 ou un entier positif.")
            return

    announce_delay_seconds = announce_delay_minutes * 60
    error = await run_quiz(
        ctx.channel,
        categorie,
        question_count,
        announce_delay_seconds,
    )
    if error:
        await ctx.send(error)


@bot.tree.command(name="pause", description="Met en pause ou reprend le quizz")
async def pause_quiz(interaction: discord.Interaction):
    if interaction.guild is None:
        await send_interaction_message(
            interaction,
            "Cette commande doit etre lancee dans un serveur.",
            ephemeral=True,
        )
        return

    if not can_manage_quiz(interaction.user, interaction.guild):
        await send_interaction_message(
            interaction,
            "Tu dois etre proprietaire du serveur, admin, ou avoir 'Gerer le serveur' pour mettre en pause.",
            ephemeral=True,
        )
        return

    session = active_quizzes.get(interaction.channel_id)
    if not session:
        await send_interaction_message(interaction, "Aucun quizz en cours dans ce salon.", ephemeral=True)
        return

    paused = session.toggle_pause()
    status = "en pause" if paused else "repris"
    await send_interaction_message(interaction, f"Quizz {status}.", ephemeral=True)


@bot.tree.command(name="stop", description="Stoppe le quizz en cours")
async def stop_quiz(interaction: discord.Interaction):
    if interaction.guild is None:
        await send_interaction_message(
            interaction,
            "Cette commande doit etre lancee dans un serveur.",
            ephemeral=True,
        )
        return

    if not can_manage_quiz(interaction.user, interaction.guild):
        await send_interaction_message(
            interaction,
            "Tu dois etre proprietaire du serveur, admin, ou avoir 'Gerer le serveur' pour stopper le quizz.",
            ephemeral=True,
        )
        return

    session = active_quizzes.get(interaction.channel_id)
    if not session:
        await send_interaction_message(interaction, "Aucun quizz en cours dans ce salon.", ephemeral=True)
        return

    session.stop()
    if session.current_view:
        await session.current_view.reveal_answer()
        session.current_view.stop()

    await send_interaction_message(interaction, "Quizz arrete.", ephemeral=True)
    await interaction.channel.send("Quizz arrete.")


@bot.tree.command(name="resync", description="Resynchronise les commandes")
async def resync_commands(interaction: discord.Interaction):
    if interaction.guild is None:
        await send_interaction_message(
            interaction,
            "Cette commande doit etre lancee dans un serveur.",
            ephemeral=True,
        )
        return

    if not can_manage_quiz(interaction.user, interaction.guild):
        await send_interaction_message(
            interaction,
            "Tu dois etre proprietaire du serveur, admin, ou avoir 'Gerer le serveur' pour resynchroniser.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    if QUIZ_GUILD_ID:
        try:
            guild_id = int(QUIZ_GUILD_ID)
        except ValueError:
            await interaction.followup.send(
                "Config invalide: QUIZ_GUILD_ID.", ephemeral=True
            )
            return

        guild = discord.Object(id=guild_id)
        bot.tree.copy_global_to(guild=guild)
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        await bot.tree.sync(guild=guild)
        await interaction.followup.send(
            "Commandes resynchronisees pour ce serveur.",
            ephemeral=True,
        )
        return

    await bot.tree.sync()
    await interaction.followup.send(
        "Commandes resynchronisees.", ephemeral=True
    )


@bot.tree.command(name="historique", description="Affiche les derniers quiz")
@app_commands.describe(nombre="Nombre de quiz a afficher (defaut: 5)")
async def quiz_history(interaction: discord.Interaction, nombre: Optional[int] = 5):
    await interaction.response.defer(ephemeral=True)
    
    history = load_quiz_history()
    if not history:
        await interaction.followup.send("Aucun historique disponible.", ephemeral=True)
        return
    
    if nombre is None or nombre < 1:
        nombre = 5
    
    recent_quizzes = history[-nombre:][::-1]
    
    entries = []
    for idx, quiz in enumerate(reversed(history[-nombre:]), start=1):
        timestamp = quiz.get("timestamp", "?")
        categorie = quiz.get("categorie", "?")
        nb_participants = len(quiz.get("participants", []))
        
        try:
            dt = datetime.fromisoformat(timestamp)
            formatted_time = dt.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            formatted_time = timestamp
        
        entries.append(
            f"**{idx}.** {categorie.upper()} — {nb_participants} participants — {formatted_time}"
        )
    
    embed = discord.Embed(
        title=f"Historique des {len(recent_quizzes)} derniers quiz",
        description="\n".join(entries),
        color=discord.Color.blue(),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="quiz-details", description="Affiche les details d'un quiz")
@app_commands.describe(numero="Numero du quiz (1 = plus recent)")
async def quiz_details(interaction: discord.Interaction, numero: int):
    await interaction.response.defer(ephemeral=True)
    
    history = load_quiz_history()
    if not history:
        await interaction.followup.send("Aucun historique disponible.", ephemeral=True)
        return
    
    if numero < 1 or numero > len(history):
        await interaction.followup.send(
            f"Numero invalide. Veuillez choisir entre 1 et {len(history)}.",
            ephemeral=True,
        )
        return
    
    quiz = history[-(numero)]
    timestamp = quiz.get("timestamp", "?")
    categorie = quiz.get("categorie", "?")
    participants = quiz.get("participants", [])
    
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_time = dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        formatted_time = timestamp
    
    leaderboard = []
    for rank, participant in enumerate(participants, start=1):
        user_id = participant.get("user_id")
        points = participant.get("points", 0)
        try:
            user = await bot.fetch_user(user_id)
            leaderboard.append(f"**{rank}.** {user.name} — {points} {POINTS_EMOJI}")
        except Exception:
            leaderboard.append(f"**{rank}.** User#{user_id} — {points} {POINTS_EMOJI}")
    
    embed = discord.Embed(
        title=f"Details: {categorie.upper()}",
        description="\n".join(leaderboard) if leaderboard else "Aucun participant.",
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"Date: {formatted_time}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandSignatureMismatch):
        try:
            if interaction.guild is not None:
                guild = discord.Object(id=interaction.guild.id)
                bot.tree.copy_global_to(guild=guild)
                await bot.tree.sync(guild=guild)
            else:
                await bot.tree.sync()
        except Exception:
            pass
        return

    if is_ignorable_interaction_error(error):
        return
    raise error


@bot.command()
async def score(ctx):
    """Affiche ton score. Utilise : !score"""
    points = scores.get(ctx.author.id, 0)
    await ctx.send(f"{ctx.author.mention}, tu as **{points}** {POINTS_EMOJI}.")


@bot.command()
async def classement(ctx):
    """Affiche le classement. Utilise : !classement"""
    if not scores:
        await ctx.send("Personne n'a encore joué !")
        return

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    leaderboard = []
    for i, (user_id, pts) in enumerate(sorted_scores, start=1):
        user = await bot.fetch_user(user_id)
        leaderboard.append(f"**{i}.** {user.name} — {pts} {POINTS_EMOJI}")

    embed = discord.Embed(
        title="Classement",
        description="\n".join(leaderboard),
        color=discord.Color.gold(),
    )
    await ctx.send(embed=embed)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    raise error


bot.run(os.getenv("DISCORD_TOKEN"))
