import os

import requests

SAVE_DIR = "assets/image/item_images"
os.makedirs(SAVE_DIR, exist_ok=True)

# 1. 최신 버전 확인
version = requests.get("https://ddragon.leagueoflegends.com/api/versions.json").json()[
    0
]

print("Data Dragon 버전:", version)

# 2. 아이템 데이터
item_url = f"https://ddragon.leagueoflegends.com/cdn/" f"{version}/data/ko_KR/item.json"

items = requests.get(item_url).json()["data"]

print("아이템 수:", len(items))

# 3. 이미지 다운로드
for item_id, item in items.items():

    image_name = item["image"]["full"]

    image_url = (
        f"https://ddragon.leagueoflegends.com/cdn/" f"{version}/img/item/{image_name}"
    )

    response = requests.get(image_url)

    save_path = os.path.join(SAVE_DIR, image_name)

    with open(save_path, "wb") as f:
        f.write(response.content)

    print(f'{item_id} | {item["name"]} -> {image_name}')

print("완료")
