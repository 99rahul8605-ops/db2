# Copy into your EXISTING Pyrogram upload bot if you want /lookup there too.
# Add LOOKUP_API_URL and LOOKUP_API_KEY to that bot's .env.
import os, httpx
from pyrogram import filters
LOOKUP_API_URL=os.getenv("LOOKUP_API_URL","http://127.0.0.1:8080").rstrip("/")
LOOKUP_API_KEY=os.getenv("LOOKUP_API_KEY","")

@app.on_message(filters.command("lookup") & filters.user(list(ADMIN_IDS)))
async def lookup_cmd(client,message):
    parts=(message.text or "").split(maxsplit=1)
    if len(parts)<2: await message.reply_text("Usage: /lookup <number>"); return
    async with httpx.AsyncClient(timeout=30) as c:
        r=await c.get(LOOKUP_API_URL+"/lookup",params={"number":parts[1]},headers={"X-API-Key":LOOKUP_API_KEY})
    if r.status_code!=200: await message.reply_text(f"API error {r.status_code}: {r.text[:500]}"); return
    data=r.json()
    if not data.get("found"): await message.reply_text("No record found."); return
    out=[]
    for i,x in enumerate(data["results"],1):
        out.append(f"#{i}\nMobile: {x.get('mobile','-')}\nName: {x.get('name','-')}\nFather: {x.get('fname','-')}\nAddress: {x.get('address','-')}\nAlt: {x.get('alt','-')}\nCircle: {x.get('circle','-')}\nID: {x.get('id','-')}\nEmail: {x.get('email','-')}")
    await message.reply_text((f"Found {data['count']} record(s)\n\n"+"\n\n".join(out))[:4000])
