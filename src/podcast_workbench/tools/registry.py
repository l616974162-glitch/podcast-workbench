"""配置协议名到模板客户端的注册。"""

import httpx

from podcast_workbench.config import TTSConfig
from podcast_workbench.tools.base import ToolClient
from podcast_workbench.tools.qwen_tts import QwenTTSClient

CLIENTS: dict[str, type[ToolClient]] = {"qwen_tts": QwenTTSClient}


def create_tts_client(config: TTSConfig, http: httpx.AsyncClient) -> ToolClient:
    return CLIENTS[config.protocol](config, http)
