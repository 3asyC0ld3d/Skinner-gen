import os
import asyncio
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from datetime import datetime, timezone
from keep_alive import keep_alive
# Load variables from .env
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_CHANNEL_ID = int(os.getenv("ALLOWED_CHANNEL_ID"))
ALLOWED_ROLE_ID = int(os.getenv("ALLOWED_ROLE_ID"))
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
MIN_ACCOUNT_AGE_DAYS = int(os.getenv("MIN_ACCOUNT_AGE_DAYS", 7))
MIN_JOIN_AGE_DAYS = int(os.getenv("MIN_JOIN_AGE_DAYS", 1))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ===============================
# GLOBAL STORAGE & LOCKS
# ===============================
file_locks = {
    "vcc.txt": asyncio.Lock(),
    "mcacc.txt": asyncio.Lock()
}
stock_cache = {"vcc.txt": 0, "mcacc.txt": 0}

# ===============================
# HELPER FUNCTIONS
# ===============================
def count_stock(filename):
    if not os.path.exists(filename):
        return 0
    with open(filename, "r", encoding="utf-8") as f:
        return len([line for line in f if line.strip()])

async def get_reward(filename):
    async with file_locks[filename]:
        if not os.path.exists(filename): return None
        with open(filename, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if not lines: return None
        
        reward = lines[0].strip()
        with open(filename, "w", encoding="utf-8") as f:
            f.writelines(lines[1:])
        return reward

@tasks.loop(seconds=60)
async def refresh_stock_loop():
    stock_cache["vcc.txt"] = count_stock("vcc.txt")
    stock_cache["mcacc.txt"] = count_stock("mcacc.txt")

# ===============================
# PREMIUM EMBED BUILDER
# ===============================
async def send_fancy_delivery(user, item_name, reward_data):
    bot_user = await bot.fetch_user(bot.user.id)
    embed = discord.Embed(
        title=f"✨ {item_name} Generated!",
        description="Your requested account details are below. Keep them secure.",
        color=0x8f00ff,
        timestamp=datetime.now()
    )
    embed.add_field(name="🔑 Account Info", value=f"```\n{reward_data}\n```", inline=False)
    embed.set_thumbnail(url=user.display_avatar.url)
    
    # Check for bot banner
    if bot_user.banner:
        embed.set_image(url=bot_user.banner.url)
    
    embed.set_footer(text=f"Requested by {user.name}", icon_url=user.display_avatar.url)
    await user.send(embed=embed)

# ===============================
# USER COMMAND: !gen
# ===============================
@bot.command(name="gen")
@commands.cooldown(1, 120, commands.BucketType.user)
async def gen(ctx, type: str = None):
    # Validations
    if ctx.channel.id != ALLOWED_CHANNEL_ID: return
    if ALLOWED_ROLE_ID not in [role.id for role in ctx.author.roles]:
        return await ctx.send("❌ You don't have permission.", delete_after=5)
    
    if type is None or type.lower() not in ["vcc", "mcacc"]:
        gen.reset_cooldown(ctx)
        return await ctx.send("❓ Usage: `!gen vcc` or `!gen mcacc`", delete_after=5)

    # Anti-Alt
    now = datetime.now(timezone.utc)
    if (now - ctx.author.created_at).days < MIN_ACCOUNT_AGE_DAYS:
        return await ctx.send("🚫 Account too new.", delete_after=10)

    filename = "vcc.txt" if type.lower() == "vcc" else "mcacc.txt"
    reward = await get_reward(filename)
    
    if not reward:
        return await ctx.send(f"❌ {type.upper()} is out of stock!", delete_after=5)

    try:
        await send_fancy_delivery(ctx.author, type.upper(), reward)
        await ctx.send(f"✅ {ctx.author.mention}, check your DMs!", delete_after=10)
        
        # Logging
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(f"📜 **Claim:** {ctx.author.name} generated a {type.upper()}")
    except discord.Forbidden:
        await ctx.send("❌ Enable your DMs and try again!", delete_after=10)

# ===============================
# ADMIN COMMAND: !restock
# ===============================
@bot.command(name="restock")
async def restock(ctx):
    has_admin = any(role.id == ADMIN_ROLE_ID for role in ctx.author.roles)
    if not has_admin:
        return await ctx.send("❌ Admin only command.", delete_after=5)

    prompt_embed = discord.Embed(title="📂 Restock", description="Reply with **VCC** or **MCACC**.", color=0x8f00ff)
    main_msg = await ctx.send(embed=prompt_embed)

    def check_msg(m): return m.author == ctx.author and m.channel == ctx.channel

    try:
        # Step 1: Get Type
        m_type = await bot.wait_for("message", check=lambda m: check_msg(m) and m.content.upper() in ["VCC", "MCACC"], timeout=30)
        stock_type = m_type.content.upper()
        filename = "vcc.txt" if stock_type == "VCC" else "mcacc.txt"
        await m_type.delete()

        # Step 2: Get File
        await main_msg.edit(embed=discord.Embed(title=f"📥 Uploading {stock_type}", description="Attach your `.txt` file now.", color=0x00ffcc))
        m_file = await bot.wait_for("message", check=lambda m: check_msg(m) and m.attachments, timeout=60)
        attachment = m_file.attachments[0]

        if not attachment.filename.endswith(".txt"):
            return await ctx.send("❌ Invalid file format.", delete_after=5)

        # Step 3: Append
        raw_data = await attachment.read()
        new_lines = raw_data.decode("utf-8").strip().splitlines()
        
        async with file_locks[filename]:
            with open(filename, "a", encoding="utf-8") as f:
                f.write("\n" + "\n".join(new_lines))

        await m_file.delete()
        stock_cache[filename] = count_stock(filename)
        
        await main_msg.edit(embed=discord.Embed(title="✅ Success", description=f"Added **{len(new_lines)}** items to {stock_type}.", color=0x00ff00))

    except asyncio.TimeoutError:
        await main_msg.edit(content="⏳ Timed out.", embed=None)

# ===============================
# ADMIN COMMAND: !stock
# ===============================
@bot.command(name="stock")
async def stock_check(ctx):
    embed = discord.Embed(title="📦 Current Stock Levels", color=0x8f00ff)
    embed.add_field(name="💳 VCC", value=f"`{stock_cache['vcc.txt']}` available")
    embed.add_field(name="🎮 MCACC", value=f"`{stock_cache['mcacc.txt']}` available")
    await ctx.send(embed=embed)

# ===============================
# EVENTS & ERRORS
# ===============================
@gen.error
async def gen_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Cooldown: {error.retry_after:.0f}s left.", delete_after=5)

@bot.event
async def on_ready():
    refresh_stock_loop.start()
    print(f"Logged in as {bot.user}")

bot.run(TOKEN)