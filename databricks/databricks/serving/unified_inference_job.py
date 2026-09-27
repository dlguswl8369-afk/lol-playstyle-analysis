# Databricks notebook source
import sys
import json

# ============================================
# 1. 프로젝트 경로 등록
# ============================================

PROJECT_ROOT = "/Workspace/Users/5dt008@msacademy.msai.kr"

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ============================================
# 2. 프로젝트 모듈 import
# ============================================

from databricks.serving.inference_adapters import DatabricksDeltaAdapter
from databricks.serving.unified_inference_notebook import notebook_main


# ============================================
# 3. Azure OpenAI Client
# ============================================

from openai import OpenAI


class AzureOpenAILLMClient:
    def __init__(self, *, endpoint, api_key, deployment):
        self.deployment = deployment

        self.client = OpenAI(
            api_key=api_key,
            base_url=f"{endpoint.rstrip('/')}/openai/v1/",
        )

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        response = self.client.responses.create(
            model=self.deployment,
            instructions=system_prompt,
            input=user_prompt,
        )

        text = response.output_text

        if not text or not text.strip():
            raise RuntimeError("Azure OpenAI returned an empty response")

        return text.strip()


# ============================================
# 4. Job Parameters
# ============================================

dbutils.widgets.text("player_id", "")
dbutils.widgets.text("recent_games_json", "")
dbutils.widgets.text("tier", "UNKNOWN")


# ============================================
# 5. Data Adapter 생성
# ============================================

data_adapter = DatabricksDeltaAdapter(
    spark_session=spark,
    catalog="lol_insight",
)


# ============================================
# 6. Azure OpenAI 연결
# ============================================

AZURE_OPENAI_ENDPOINT = "https://5dt-team3-lol-openai-dev.openai.azure.com"
AZURE_OPENAI_DEPLOYMENT = "gpt-5-mini-rag"

# 테스트 단계에서는 아래 부분에 키 입력
# Job 정상 동작 확인 후 Secret Scope로 변경
AZURE_OPENAI_API_KEY = dbutils.secrets.get(
    scope="lol-ai-secrets",
    key="azure-openai-api-key"
)

llm_client = AzureOpenAILLMClient(
    endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_API_KEY,
    deployment=AZURE_OPENAI_DEPLOYMENT,
)


# ============================================
# 7. Unified Inference 실행
# ============================================

notebook_main(
    dbutils=dbutils,
    data_adapter=data_adapter,
    llm_client=llm_client,
)