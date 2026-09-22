import os

import requests

SAVE_DIR = "assets/image/champion_images"  # 챔피언 이미지 저장 폴더
os.makedirs(SAVE_DIR, exist_ok=True)

# 1. 최신 Data Dragon 버전
versions_url = "https://ddragon.leagueoflegends.com/api/versions.json"
version = requests.get(versions_url).json()[0]

print("Data Dragon 버전:", version)

# 2. 전체 챔피언 목록
champion_url = (
    f"https://ddragon.leagueoflegends.com/cdn/"
    f"{version}/data/ko_KR/champion.json"
)

champion_data = requests.get(champion_url).json()["data"]

print("챔피언 수:", len(champion_data))

# 3. 이미지 전체 다운로드
for champion in champion_data.values():

    champion_id = champion["id"]
    champion_name = champion["name"]
    image_name = champion["image"]["full"]

    image_url = (
        f"https://ddragon.leagueoflegends.com/cdn/"
        f"{version}/img/champion/{image_name}"
    )

    image = requests.get(image_url)

    # 폴더 위치와 이미지 파일 이름을 결합하여 저장 경로 생성
    save_path = os.path.join(SAVE_DIR, image_name)

    # 파일 작업이 끝나면 자동으로 파일을 닫음
    with open(save_path, "wb") as f:
        f.write(image.content)

    print(f"{champion_name} -> {image_name}")

print("완료")
