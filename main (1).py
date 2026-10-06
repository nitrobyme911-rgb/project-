import discord
from discord.ext import commands
from discord import app_commands
import datetime
import asyncio
import aiohttp
import json
import os
import uuid

# Set up intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Database file paths
CONFIG_FILE = "adv_config.json"
KEYS_FILE = "adv_keys.json"

# Global database variables
campaigns = {}
serial_keys = {}

# ⚠️ REPLACE THIS WITH YOUR ACTUAL NUMERIC DISCORD USER ID
BOT_OWNERS = []

def load_data():
    global campaigns, serial_keys
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                raw = json.load(f)
                campaigns = {int(k): v for k, v in raw.items()}
                # Reset transient background execution tasks on boot
                for uid in campaigns:
                    if "slots_allowed" not in campaigns[uid]:
                        campaigns[uid]["slots_allowed"] = 1
                    for slot in ["1", "2", "3", "4", "5"]:
                        if slot in campaigns[uid]:
                            campaigns[uid][slot]["active"] = False
                            if "success" not in campaigns[uid][slot]: campaigns[uid][slot]["success"] = 0
                            if "fail" not in campaigns[uid][slot]: campaigns[uid][slot]["fail"] = 0
        except:
            campaigns = {}
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, 'r') as f:
                serial_keys = json.load(f)
        except:
            serial_keys = {}

def save_data():
    clean_cam = {}
    for uid, data in campaigns.items():
        clean_cam[uid] = {"slots_allowed": data.get("slots_allowed", 1)}
        for slot in ["1", "2", "3", "4", "5"]:
            if slot in data:
                s_data = data[slot]
                clean_cam[uid][slot] = {
                    "token": s_data.get("token"),
                    "channel": s_data.get("channel"),
                    "time": s_data.get("time"),
                    "message": s_data.get("message", "Auto Advertisement"),
                    "success": s_data.get("success", 0),
                    "fail": s_data.get("fail", 0)
                }
    with open(CONFIG_FILE, 'w') as f:
        json.dump(clean_cam, f, indent=4)
    with open(KEYS_FILE, 'w') as f:
        json.dump(serial_keys, f, indent=4)

def check_subscription(user_id):
    if user_id in BOT_OWNERS:
        return True, 5  # Owners get max slots
    for key, data in serial_keys.items():
        if data.get("redeemed_by") == user_id:
            expires = data.get("expires_at")
            if expires == "lifetime":
                return True, data.get("slots_allowed", 1)
            if expires and datetime.datetime.fromisoformat(expires) > datetime.datetime.utcnow():
                return True, data.get("slots_allowed", 1)
    return False, 0

async def adv_worker(user_id: int, slot: str):
    while True:
        try:
            if user_id not in campaigns or slot not in campaigns[user_id] or not campaigns[user_id][slot].get("active"):
                break
                
            data = campaigns[user_id][slot]
            token = data["token"]
            channel_id = data["channel"]
            interval = max(10, int(data["time"])) # Boundary protection safeguard
            msg_content = data["message"]

            url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
            headers = {"Authorization": token, "Content-Type": "application/json"}
            payload = {"content": msg_content}

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200 or resp.status == 201:
                        campaigns[user_id][slot]["success"] += 1
                    elif resp.status == 429: # Rate Limited / Account Lock Out
                        campaigns[user_id][slot]["fail"] += 1
                        campaigns[user_id][slot]["active"] = False
                        try:
                            user = await bot.fetch_user(user_id)
                            await user.send(f"⚠️ **ALERT:** Auto-advertising loop on **Slot {slot}** has been automatically **STOPPED** due to an account limit boundary restriction (HTTP 429).")
                        except:
                            pass
                        break
                    else:
                        campaigns[user_id][slot]["fail"] += 1
            
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(15)

@bot.event
async def on_ready():
    load_data()
    # Clears out potential duplicate menus by registering only globally
    await bot.tree.sync()
    print(f"Bot Application Online: {bot.user.name}")
    print("Slash Commands Synchronized Globally!")

# --- MANDATORY COMMAND INTERACTION ROUTINES ---

@bot.tree.command(name="redeem", description="Redeem a premium license access key")
async def redeem(interaction: discord.Interaction, key: str):
    await interaction.response.defer(ephemeral=True) # Defer immediately to fix response timed out error
    
    if key not in serial_keys:
        return await interaction.followup.send("❌ Invalid key sequence.", ephemeral=True)
    if serial_keys[key]["redeemed_by"] is not None:
        return await interaction.followup.send("❌ This key has already been claimed.", ephemeral=True)
    
    days = serial_keys[key]["duration_days"]
    slots = serial_keys[key]["slots_allowed"]
    
    if days == -1:
        exp_str = "lifetime"
    else:
        exp_str = (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat()
        
    serial_keys[key]["redeemed_by"] = interaction.user.id
    serial_keys[key]["expires_at"] = exp_str
    
    uid = interaction.user.id
    if uid not in campaigns:
        campaigns[uid] = {}
    campaigns[uid]["slots_allowed"] = slots
    
    save_data()
    await interaction.followup.send(f"✅ Key redeemed successfully!\n**Duration:** {f'{days} Days' if days != -1 else 'Lifetime'}\n**Allowed Config Slots:** {slots}", ephemeral=True)

@bot.tree.command(name="generate_key", description="[OWNER ONLY] Generate a license token")
@app_commands.choices(duration=[
    app_commands.Choice(name="1 Day", value=1),
    app_commands.Choice(name="5 Days", value=5),
    app_commands.Choice(name="10 Days", value=10),
    app_commands.Choice(name="1 Month", value=30),
    app_commands.Choice(name="Lifetime", value=-1)
], config_slots=[
    app_commands.Choice(name="1 Config Slot", value=1),
    app_commands.Choice(name="2 Config Slots", value=2),
    app_commands.Choice(name="3 Config Slots", value=3),
    app_commands.Choice(name="4 Config Slots", value=4),
    app_commands.Choice(name="5 Config Slots", value=5)
])
async def generate_key(interaction: discord.Interaction, duration: app_commands.Choice[int], config_slots: app_commands.Choice[int]):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id not in BOT_OWNERS:
        return await interaction.followup.send("❌ Access Denied: Administrator clearance mandatory.", ephemeral=True)
        
    new_key = f"ADV-{str(uuid.uuid4()).upper()[:13]}"
    serial_keys[new_key] = {"duration_days": duration.value, "slots_allowed": config_slots.value, "redeemed_by": None, "expires_at": None}
    save_data()
    
    await interaction.followup.send(f"🔑 **Generated Key:** `{new_key}`\n**Tier:** {duration.name} with {config_slots.name}", ephemeral=True)

@bot.tree.command(name="usertoken", description="Bind an advertising user account token to a specific configuration slot")
@app_commands.choices(slot=[
    app_commands.Choice(name="Slot 1", value="1"),
    app_commands.Choice(name="Slot 2", value="2"),
    app_commands.Choice(name="Slot 3", value="3"),
    app_commands.Choice(name="Slot 4", value="4"),
    app_commands.Choice(name="Slot 5", value="5")
])
async def usertoken(interaction: discord.Interaction, slot: app_commands.Choice[str], token: str):
    await interaction.response.defer(ephemeral=True)
    is_valid, slots_allowed = check_subscription(interaction.user.id)
    if not is_valid:
        return await interaction.followup.send("❌ Active premium license required to use commands.", ephemeral=True)
    
    if int(slot.value) > slots_allowed:
        return await interaction.followup.send(f"❌ Your license level only allows configuration up to **Slot {slots_allowed}**.", ephemeral=True)
        
    uid = interaction.user.id
    if uid not in campaigns: 
        campaigns[uid] = {"slots_allowed": slots_allowed}
        
    if slot.value not in campaigns[uid]: 
        campaigns[uid][slot.value] = {"success": 0, "fail": 0, "active": False}
    
    campaigns[uid][slot.value]["token"] = token
    save_data()
    await interaction.followup.send(f"✅ Token assigned safely to **Slot {slot.value}**.", ephemeral=True)

@bot.tree.command(name="config", description="Configure transmission details for an advertising slot")
@app_commands.choices(slot=[
    app_commands.Choice(name="Slot 1", value="1"),
    app_commands.Choice(name="Slot 2", value="2"),
    app_commands.Choice(name="Slot 3", value="3"),
    app_commands.Choice(name="Slot 4", value="4"),
    app_commands.Choice(name="Slot 5", value="5")
])
async def config(interaction: discord.Interaction, slot: app_commands.Choice[str], channel_id: str, interval_seconds: int, message: str):
    await interaction.response.defer(ephemeral=True)
    is_valid, slots_allowed = check_subscription(interaction.user.id)
    if not is_valid:
        return await interaction.followup.send("❌ Active premium license required.", ephemeral=True)
        
    if int(slot.value) > slots_allowed:
        return await interaction.followup.send(f"❌ Your license level only allows configuration up to **Slot {slots_allowed}**.", ephemeral=True)
        
    uid = interaction.user.id
    if uid not in campaigns or slot.value not in campaigns[uid]:
        return await interaction.followup.send(f"❌ Set user account token via `/usertoken` for **Slot {slot.value}** first.", ephemeral=True)
        
    campaigns[uid][slot.value]["channel"] = channel_id
    campaigns[uid][slot.value]["time"] = interval_seconds
    campaigns[uid][slot.value]["message"] = message
    save_data()
    
    await interaction.followup.send(f"✅ Configuration verified and saved for **Slot {slot.value}**.", ephemeral=True)

@bot.tree.command(name="start", description="Activate background advertisement campaign delivery")
@app_commands.choices(slot=[
    app_commands.Choice(name="Slot 1", value="1"),
    app_commands.Choice(name="Slot 2", value="2"),
    app_commands.Choice(name="Slot 3", value="3"),
    app_commands.Choice(name="Slot 4", value="4"),
    app_commands.Choice(name="Slot 5", value="5")
])
async def start(interaction: discord.Interaction, slot: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    is_valid, slots_allowed = check_subscription(interaction.user.id)
    if not is_valid:
        return await interaction.followup.send("❌ Active premium license required.", ephemeral=True)
        
    if int(slot.value) > slots_allowed:
        return await interaction.followup.send(f"❌ Action denied by permissions matrix.", ephemeral=True)
        
    uid = interaction.user.id
    if uid not in campaigns or slot.value not in campaigns[uid] or "token" not in campaigns[uid][slot.value] or "channel" not in campaigns[uid][slot.value]:
        return await interaction.followup.send("❌ Setup settings parameters inside `/usertoken` and `/config` before activation.", ephemeral=True)
        
    if campaigns[uid][slot.value].get("active"):
        return await interaction.followup.send(f"ℹ️ Advertising engine for **Slot {slot.value}** is already actively running.", ephemeral=True)

    campaigns[uid][slot.value]["active"] = True
    loop = asyncio.get_running_loop()
    task = loop.create_task(adv_worker(uid, slot.value))
    campaigns[uid][slot.value]["task"] = task
    
    await interaction.followup.send(f"🚀 Advertising loop task **Slot {slot.value}** started successfully!", ephemeral=True)

@bot.tree.command(name="stop", description="Halt an active advertisement campaign")
@app_commands.choices(slot=[
    app_commands.Choice(name="Slot 1", value="1"),
    app_commands.Choice(name="Slot 2", value="2"),
    app_commands.Choice(name="Slot 3", value="3"),
    app_commands.Choice(name="Slot 4", value="4"),
    app_commands.Choice(name="Slot 5", value="5")
])
async def stop(interaction: discord.Interaction, slot: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    uid = interaction.user.id
    if uid not in campaigns or slot.value not in campaigns[uid] or not campaigns[uid][slot.value].get("active"):
        return await interaction.followup.send(f"ℹ️ Advertising engine loop on **Slot {slot.value}** is not active.", ephemeral=True)
        
    campaigns[uid][slot.value]["active"] = False
    if "task" in campaigns[uid][slot.value]:
        campaigns[uid][slot.value]["task"].cancel()
        
    await interaction.followup.send(f"🛑 Advertising loop engine for **Slot {slot.value}** stopped manually.", ephemeral=True)

@bot.tree.command(name="check", description="View analytics metrics and system diagnostic health status")
async def check(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    uid = interaction.user.id
    if uid not in campaigns or not campaigns[uid]:
        return await interaction.followup.send("❌ No configured slots or analytics telemetry maps found.", ephemeral=True)
        
    embed = discord.Embed(title="📋 Campaign Profile & Health Diagnostics", color=discord.Color.purple())
    slots_allowed = campaigns[uid].get("slots_allowed", 1)
    embed.description = f"**License Authorization Level:** Up to {slots_allowed} Active Config Slots Allowed"
    
    for slot in ["1", "2", "3", "4", "5"]:
        if slot in campaigns[uid]:
            data = campaigns[uid][slot]
            status = "RUNNING 🟢" if data.get("active") else "STOPPED 🔴"
            chan = data.get("channel", "Not Configured")
            success_count = data.get("success", 0)
            fail_count = data.get("fail", 0)
            
            embed.add_field(
                name=f"Slot Profile {slot}",
                value=f"**Status:** {status}\n**Target Channel:** `{chan}`\n✅ **Sent:** `{success_count}` | ❌ **Failed:** `{fail_count}`",
                inline=False
            )
            
    await interaction.followup.send(embed=embed, ephemeral=True)

# Launch runtime loop environment
bot.run("YOUR_BOT_TOKEN_HERE")
