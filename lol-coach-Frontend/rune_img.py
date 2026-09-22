import os

import requests

SAVE_DIR = "assets/image/rune_images"

# 최신 Data Dragon 버전
version = requests.get("https://ddragon.leagueoflegends.com/api/versions.json").json()[0]

# 룬 데이터
url = (
    f"https://ddragon.leagueoflegends.com/cdn/"
    f"{version}/data/ko_KR/runesReforged.json"
)

rune_data = requests.get(url).json()

os.makedirs(SAVE_DIR, exist_ok=True)

# 룬 계열 저장 폴더
PATH_DIR = os.path.join(SAVE_DIR, "paths")
os.makedirs(PATH_DIR, exist_ok=True)

# 개별 룬 저장 폴더
RUNE_DIR = os.path.join(SAVE_DIR, "runes")
os.makedirs(RUNE_DIR, exist_ok=True)


def download_image(image_path, save_path):
    image_url = f"https://ddragon.leagueoflegends.com/cdn/img/" f"{image_path}"

    response = requests.get(image_url)
    response.raise_for_status()

    with open(save_path, "wb") as f:
        f.write(response.content)


for rune_path in rune_data:

    # =========================
    # 1. 룬 계열 이미지
    # =========================
    path_id = rune_path["id"]
    path_name = rune_path["name"]
    path_icon = rune_path["icon"]

    path_save = os.path.join(PATH_DIR, f"{path_id}.png")

    download_image(path_icon, path_save)

    print(f"[계열] {path_name} ({path_id}) -> {path_save}")

    # =========================
    # 2. 개별 룬 이미지
    # =========================
    for slot in rune_path["slots"]:

        for rune in slot["runes"]:

            rune_id = rune["id"]
            rune_name = rune["name"]
            rune_icon = rune["icon"]

            rune_save = os.path.join(RUNE_DIR, f"{rune_id}.png")

            download_image(rune_icon, rune_save)

            print(f"[룬] {rune_name} ({rune_id}) -> {rune_save}")


print("룬 이미지 수집 완료!")
