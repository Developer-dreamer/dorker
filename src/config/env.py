from os import getenv
from sys import exit

from pydantic import BaseModel


class Paths(BaseModel):
    master_prompt: str
    payload_scraper: str
    payload_user: str
    profile: str

class Env(BaseModel):
    bot_token: str
    gemini_api_key: str
    paths: Paths


def get_envs() -> Env:

    BOT_TOKEN = getenv("TELEGRAM_API_KEY")
    if not BOT_TOKEN:
        print("Error: TELEGRAM_API_KEY variable is not set.")
        exit(1)

    GEMINI_API_KEY = getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY variable is not set.")
        exit(1)


    MASTER_PROMPT = getenv("MASTER_PROMPT")
    if not MASTER_PROMPT:
        print("Error: MASTER_PROMPT variable is not set.")
        exit(1)

    PAYLOAD_BOT = getenv("PAYLOAD_BOT")
    if not PAYLOAD_BOT:
        print("Error: PAYLOAD_BOT variable is not set.")
        exit(1)

    PAYLOAD_USER = getenv("PAYLOAD_USER")
    if not PAYLOAD_USER:
        print("Error: PAYLOAD_USER variable is not set.")
        exit(1)

    PROFILE = getenv("PROFILE")
    if not PROFILE:
        print("Error: PAYLOAD_PROFILE variable is not set.")
        exit(1)

    env = Env(
        bot_token=BOT_TOKEN,
        gemini_api_key=GEMINI_API_KEY,
        paths=Paths(
            master_prompt=MASTER_PROMPT,
            payload_scraper=PAYLOAD_BOT,
            payload_user=PAYLOAD_USER,
            profile=PROFILE
        ))

    return env
