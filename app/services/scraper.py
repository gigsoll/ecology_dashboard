from bs4 import BeautifulSoup
import os
import requests
import re
from pathlib import Path
from app.core.config import app_config


def scrape_data() -> None:
    res = requests.get("https://opendata.gov.ua/dataset/air_monitor")
    soup = BeautifulSoup(res.text, "html.parser")
    data_dir = app_config.data_dir

    Path(data_dir).mkdir(parents=True, exist_ok=True)

    # Select only names of the files
    regex: str = r"\d{4}-\d{2}-\d{2}"

    for item in soup.find_all("div", {"class": "resource-item"}):
        name_block = item.find("div", {"class": "data-resource-name-content"})
        link_tag = name_block.find("a") if name_block else None

        if not link_tag:
            continue

        table_name_raw = link_tag.string or ""
        matches = re.findall(regex, table_name_raw)
        if not matches:
            continue

        table_name = f"{matches[0]}.csv"

        download_tag = item.find("a", {"class": "data-resource-download"}, href=True)
        if not download_tag:
            continue

        table_link = download_tag.get("href")

        file_downloaded = requests.get(table_link)
        print(file_downloaded.status_code, table_name, table_link)

        if file_downloaded.status_code == 200:
            save_path: str = os.path.join(data_dir, table_name)
            with open(save_path, "wb") as file:
                file.write(file_downloaded.content)
