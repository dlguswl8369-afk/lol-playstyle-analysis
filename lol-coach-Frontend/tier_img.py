import os
import io
import zipfile
import requests
import shutil

SAVE_DIR = "assets/image/tier_images"
os.makedirs(SAVE_DIR, exist_ok=True)

# Riot 공식 랭크 엠블럼 ZIP
ZIP_URL = "https://static.developer.riotgames.com/docs/lol/ranked-emblems-latest.zip"

# 우리가 사용할 티어
TIERS = [
    "IRON",
    "BRONZE",
    "SILVER",
    "GOLD",
    "PLATINUM",
    "EMERALD",
    "DIAMOND",
    "MASTER",
    "GRANDMASTER",
    "CHALLENGER",
]

print("티어 이미지 다운로드 중...")

response = requests.get(ZIP_URL)
response.raise_for_status()

with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:

    for tier in TIERS:
        # ZIP 내부에서 해당 티어 이미지 찾기
        matches = [
            name
            for name in zip_file.namelist()
            if tier.lower() in name.lower() and name.lower().endswith(".png")
        ]

        if not matches:
            print(f"[실패] {tier} 이미지를 찾을 수 없습니다.")
            continue

        source = matches[0]

        save_path = os.path.join(SAVE_DIR, f"{tier.lower()}.png")

        with zip_file.open(source) as src:
            with open(save_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

        print(f"{tier} -> {save_path}")

print("티어 이미지 수집 완료!")
