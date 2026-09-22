import os

import requests

SAVE_DIR = "assets/image/summoner_spell_images"
os.makedirs(SAVE_DIR, exist_ok=True)

# 최신 버전
version = requests.get("https://ddragon.leagueoflegends.com/api/versions.json").json()[
    0
]

# 소환사 주문 데이터
url = f"https://ddragon.leagueoflegends.com/cdn/" f"{version}/data/ko_KR/summoner.json"

spells = requests.get(url).json()["data"]

for spell in spells.values():

    spell_id = spell["key"]
    spell_name = spell["name"]
    image_name = spell["image"]["full"]

    image_url = (
        f"https://ddragon.leagueoflegends.com/cdn/" f"{version}/img/spell/{image_name}"
    )

    response = requests.get(image_url)
    response.raise_for_status()

    # Match API ID 기준으로 저장
    save_path = os.path.join(SAVE_DIR, f"{spell_id}.png")

    with open(save_path, "wb") as f:
        f.write(response.content)

    print(f"{spell_id} | {spell_name} -> {save_path}")

print("완료")
