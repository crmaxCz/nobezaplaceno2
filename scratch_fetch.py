import asyncio
import httpx
from bs4 import BeautifulSoup
import re

async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        import toml
        try:
            with open('C:/Users/Adam/.streamlit/secrets.toml', 'r', encoding='utf-8') as f:
                secrets = toml.load(f)
            email = secrets["moje_jmeno"]
            heslo = secrets["moje_heslo"]
        except Exception as e:
            print("Could not read secrets:", e)
            return

        resp = await client.post(
            "https://nobe.moje-autoskola.cz/index.php",
            data={"log_email": email, "log_heslo": heslo, "akce": "login"}
        )
        if "log_email" in resp.text:
            print("Login failed")
            return
        
        resp = await client.get("https://nobe.moje-autoskola.cz/admin_prednaska.php?edit_id=80901")
        with open("term_80901.html", "w", encoding="utf-8") as f:
            f.write(resp.text)
        print("Downloaded HTML to term_80901.html")

asyncio.run(main())
