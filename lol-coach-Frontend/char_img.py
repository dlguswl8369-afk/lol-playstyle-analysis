import requests
import os

SAVE_DIR = "assets/image/champion_images" # 챔피언 이미지 저장 폴더 
os.makedirs(SAVE_DIR, exist_ok=True)

# 1. 최신 Data Dragon 버전
versions_url = "https://ddragon.leagueoflegends.com/api/versions.json"
version = requests.get(versions_url).json()[0]

print("Data Dragon 버전:", version)

# 2. 전체 챔피언 목록
champion_url = (
    f"https://ddragon.leagueoflegends.com/cdn/" f"{version}/data/ko_KR/champion.json"
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

    save_path = os.path.join(SAVE_DIR, image_name) #이미지를 저장할 최종 컴퓨터 경로(폴더 위치 + 파일 이름)을 안전하게 만드는 과정

    with open(save_path, "wb") as f: # with 구문은 파일 작업이 끝나면 알아서 파일을 안전 
        f.write(image.content)

    print(f"{champion_name} -> {image_name}")

print("완료")
