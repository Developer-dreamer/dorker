from os import getenv
from pathlib import Path
from sys import exit

from pydantic import BaseModel, FilePath


class Paths(BaseModel):
    master_prompt_with_generation: FilePath
    master_prompt_matching_only: FilePath
    profile: FilePath

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


    MASTER_PROMPT_WITH_GENERATION = getenv("MASTER_PROMPT_WITH_GENERATION")
    if not MASTER_PROMPT_WITH_GENERATION:
        print("Error: MASTER_PROMPT_WITH_GENERATION variable is not set.")
        exit(1)

    MASTER_PROMPT_MATCHING_ONLY = getenv("MASTER_PROMPT_MATCHING_ONLY")
    if not MASTER_PROMPT_MATCHING_ONLY:
        print("Error: MASTER_PROMPT_MATCHING_ONLY variable is not set.")
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
            master_prompt_with_generation=Path(MASTER_PROMPT_WITH_GENERATION),
            master_prompt_matching_only=Path(MASTER_PROMPT_MATCHING_ONLY),
            profile=Path(PROFILE)
        ))

    return env
