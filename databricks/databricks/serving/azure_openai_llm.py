# Databricks notebook source
"""Azure OpenAI adapter for unified inference."""

from __future__ import annotations

import os
import sys

from openai import OpenAI


class AzureOpenAILLMClient:
    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        deployment: str | None = None,
    ):
        endpoint = endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")
        api_key = api_key or os.getenv("AZURE_OPENAI_API_KEY")
        deployment = deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT")

        if not endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT is required")

        if not api_key:
            raise ValueError("AZURE_OPENAI_API_KEY is required")

        if not deployment:
            raise ValueError("AZURE_OPENAI_DEPLOYMENT is required")

        self.deployment = deployment

        self.client = OpenAI(
            api_key=api_key,
            base_url=f"{endpoint.rstrip('/')}/openai/v1/",
        )

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        response = self.client.responses.create(
            model=self.deployment,
            instructions=system_prompt,
            input=user_prompt,
        )

        text = response.output_text

        if not text or not text.strip():
            raise RuntimeError("Azure OpenAI returned an empty response")

        return text.strip()


__all__ = ["AzureOpenAILLMClient"]

# COMMAND ----------

SERVING_PATH = "/Workspace/Users/5dt008@msacademy.msai.kr/databricks/serving"

if SERVING_PATH not in sys.path:
    sys.path.insert(0, SERVING_PATH)

print(sys.path[0])

# COMMAND ----------

llm_client = AzureOpenAILLMClient(
    endpoint="https://5dt-team3-lol-openai-dev.openai.azure.com",
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    deployment="gpt-5-mini-rag",
)

# COMMAND ----------

test = llm_client.generate(
    system_prompt="반드시 JSON만 반환하세요.",
    user_prompt='{"task":"status 키에 ok 값을 넣은 JSON만 반환하세요"}',
)

print(test)
