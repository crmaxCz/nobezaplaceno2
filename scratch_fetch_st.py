import streamlit as st
import asyncio
import httpx
import sys

async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        email = st.secrets["moje_jmeno"]
        heslo = st.secrets["moje_heslo"]
        resp = await client.post(
            "https://nobe.moje-autoskola.cz/index.php",
            data={"log_email": email, "log_heslo": heslo, "akce": "login"}
        )
        resp = await client.get("https://nobe.moje-autoskola.cz/admin_prednaska.php?edit_id=80901")
        with open("term_80901.html", "w", encoding="utf-8") as f:
            f.write(resp.text)
        print("DONE")

asyncio.run(main())
# Abort so streamlit doesn't stay open
import os
os._exit(0)
