import pathlib
from logging import Logger

import httpx


async def download_dataset_streamed(logger: Logger, url: str, destination: pathlib.Path) -> None:
    if destination.exists():
        logger.info(f"Local cache found at {destination}. Skipping download.")
        return

    logger.info(f"Streaming dataset from Cloudflare R2: {url}...")
    destination.parent.mkdir(parents=True, exist_ok=True)

    timeout = httpx.Timeout(60.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise RuntimeError(f"R2 stream failed with status: {response.status_code}")

            with open(destination, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    f.write(chunk)

    logger.info(f"Dataset stored locally at: {destination}")
