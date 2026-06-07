from env import Paths
from logger import Logger

from domain.model.prompt import PromptStructure


def load_prompts(log: Logger, paths: Paths) -> PromptStructure:
    prompts = PromptStructure()

    try:
        with open(paths.master_prompt_with_generation, "r") as f:
            prompts.master_prompt_with_generation = str.join("", f.readlines())

        with open(paths.master_prompt_matching_onlyst, "r") as f:
            prompts.master_prompt_matching_onlyst = str.join("", f.readlines())
    except FileNotFoundError as e:
        log.error("Core prompts files not found", exception=e)
        exit(1)

def load_profile(log: Logger, paths: Paths) -> str:
    try:
        with open(paths.profile, "r") as f:
            return str.join("", f.readlines())
    except FileNotFoundError as e:
        log.error("Core prompts files not found", exception=e)
        exit(1)
