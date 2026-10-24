"""配置协议名到模板客户端的注册。"""

from collections.abc import Callable

import httpx

from podcast_workbench.config import ImageConfig, TTSConfig
from podcast_workbench.tools.agnes_image import AgnesImageClient
from podcast_workbench.tools.base import SpeechClient
from podcast_workbench.tools.edge_tts import EdgeTTSClient
from podcast_workbench.tools.qwen_tts import QwenTTSClient

CLIENTS: dict[str, Callable[[TTSConfig, httpx.AsyncClient], SpeechClient]] = {
    "qwen_tts": QwenTTSClient,
    "edge_tts": EdgeTTSClient,
}
IMAGE_CLIENTS = {"agnes_image": AgnesImageClient}


def create_image_client(config: ImageConfig, http: httpx.AsyncClient) -> AgnesImageClient:
    return IMAGE_CLIENTS[config.protocol](config, http)


def create_tts_client(
    config: TTSConfig,
    http: httpx.AsyncClient,
    ffmpeg_path: str = "",
) -> SpeechClient:
    if config.protocol == "edge_tts":
        return EdgeTTSClient(config, http, ffmpeg_path)
    return CLIENTS[config.protocol](config, http)
