from os import getenv
from src.domain.model.prompt import PromptStructure
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
    db: str
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

    DB = getenv("DB")
    if not DB:
        print("Error: DB variable is not set.")
        exit(1)

    MASTER_PROMPT_WITH_GENERATION = getenv("MASTER_PROMPT_WITH_GENERATION")
    if not MASTER_PROMPT_WITH_GENERATION:
        print("Error: MASTER_PROMPT_WITH_GENERATION variable is not set.")
        exit(1)

    MASTER_PROMPT_MATCHING_ONLY = getenv("MASTER_PROMPT_MATCHING_ONLY")
    if not MASTER_PROMPT_MATCHING_ONLY:
        print("Error: MASTER_PROMPT_MATCHING_ONLY variable is not set.")
        exit(1)

    PROFILE = getenv("PROFILE")
    if not PROFILE:
        print("Error: PAYLOAD_PROFILE variable is not set.")
        exit(1)

    env = Env(
        bot_token=BOT_TOKEN,
        gemini_api_key=GEMINI_API_KEY,
        db=DB,
        paths=Paths(
            master_prompt_with_generation=Path(MASTER_PROMPT_WITH_GENERATION),
            master_prompt_matching_only=Path(MASTER_PROMPT_MATCHING_ONLY),
            profile=Path(PROFILE)
        ))

    return env

def load_prompts(path: Paths) -> PromptStructure:
    # 1. Extract the raw string payloads first
    with open(path.master_prompt_with_generation, "r") as f:
        master_gen = f.read()

    with open(path.master_prompt_matching_only, "r") as f:
        quick_match = f.read()

    # 2. Instantiate Pydantic with the completed state to pass validation
    return PromptStructure(
        master_prompt_with_generation=master_gen,
        master_prompt_quick_matching=quick_match
    )

def load_profile(path: Paths) -> str:
    with open(path.profile, "r") as f:
        return str.join("", f.readlines())
