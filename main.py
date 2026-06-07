import asyncio

from src.config.env import Env, get_envs


async def main() -> None:
    await asyncio.gather(

    )


if __name__ == "__main__":

    env: Env = get_envs()

    

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped")
