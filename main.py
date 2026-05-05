import os
import random
import time

import feedparser
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from feedgen.feed import FeedGenerator
from datetime import datetime
from dotenv import load_dotenv, dotenv_values

import re
from datetime import datetime

load_dotenv('./.env')
env = dotenv_values()


USE_SAFARI = False  

RSS_OUTPUT_DIR = env['RSS_PATH']

WINGMAN_USERNAME = env.get('WINGMAN_USERNAME', '')
WINGMAN_PASSWORD = env.get('WINGMAN_PASSWORD', '')

DATE_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def initialize_browser():
    """
    Initialize a browser. If USE_SAFARI is True, use Safari.
    Otherwise, use headless Chrome with a decent viewport.
    """
    if USE_SAFARI:
        driver = webdriver.Safari()
        driver.maximize_window()
    else:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--window-size=1920,1080")
        
        chrome_options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
        )
        driver = webdriver.Chrome(options=chrome_options)
        driver.maximize_window()
    return driver

def login(driver):
    """Login using username and password - check if redirected to login page."""
    driver.get("https://wingmanreservations.com/Scheduling/Schedule.aspx")
    time.sleep(3)
    
    # Check if we're on login page (redirected)
    if "Login" in driver.current_url or driver.find_elements(By.ID, "ctl00_body_textBoxLogin_I"):
        print("Redirected to login page, logging in...")
        
        username_field = driver.find_element(By.ID, "ctl00_body_textBoxLogin_I")
        password_field = driver.find_element(By.ID, "ctl00_body_textBoxPassword_I")
        
        username_field.clear()
        username_field.send_keys(WINGMAN_USERNAME)
        
        password_field.clear()
        password_field.send_keys(WINGMAN_PASSWORD)
        password_field.send_keys(Keys.RETURN)
        time.sleep(3)
        print("Logged in with username/password.")
    else:
        print("Already logged in.")

def get_flight_status(driver):
    """Get flight status and hover tooltip value from the Wingman Reservations page."""
    flight_status = None
    hover_value = None
    
    page_content = driver.page_source
    title_match = re.search(r"'title':'([^']*)'", page_content)
    content_match = re.search(r"'content':'([^']*)'", page_content)
    
    if title_match:
        flight_status = title_match.group(1)
        hover_value = content_match.group(1) if content_match else None
    
    if flight_status:
        return {"flight_status": flight_status, "hover_tooltip_value": hover_value}
    else:
        return {"error": "No flight status found"}

def load_existing_feed_entries(rss_file):
    """
    Parse the existing RSS file for all old entries. Returns:
      - old_feed: feedparser's result
      - existing_entries: dict keyed by the entry guid
    """
    old_posts = []
    existing_entries = {}
    if os.path.exists(rss_file):
        old_feed = feedparser.parse(rss_file)
        for entry in old_feed.entries:
            guid_val = getattr(entry, 'guid', None)
            existing_entries[guid_val] = entry
            old_posts.append({"title": entry.title, "existing": True, "link": guid_val, "date": datetime(*entry.published_parsed[:6]).strftime(DATE_FORMAT)})
    return old_posts, existing_entries

def generate_rss_feed(posts):
    """
    1) Load any existing RSS file and re-add old entries (so we keep them).
    2) Append new flight status if not already present by GUID (the link).
    3) Skip if the flight status is the same as the most recent entry.
    4) Save updated feed to wwfcstatus.xml.
    """
    os.makedirs(RSS_OUTPUT_DIR, exist_ok=True)
    rss_file = os.path.join(RSS_OUTPUT_DIR, "wwfcstatus.xml")

    old_posts, existing_entries = load_existing_feed_entries(rss_file)
    
    # Check if the new status is the same as the most recent entry
    if old_posts:
        latest_title = old_posts[0].get("title", "")
        new_title = posts[0].get("title", "") if posts else ""
        latest_desc = old_posts[0].get("description", "")
        new_desc = posts[0].get("description", "") if posts else ""
        
        title_match = latest_title == new_title
        desc_match = (not latest_desc and not new_desc) or (latest_desc == new_desc)
        
        if title_match and desc_match:
            print("Flight status unchanged. RSS feed remains unchanged.")
            return old_posts

    posts = posts + old_posts

    fg = FeedGenerator()
    fg.title("Flight Status")
    fg.link(href="https://wingmanreservations.com/Scheduling/Schedule.aspx", rel="alternate")
    fg.description("Flight status updates from Wingman Reservations")
    fg.language("en")

    new_count = 0
    for post in posts:
        fe = fg.add_entry()
        fe.link(href=post["link"], rel="alternate")
        fe.guid(post["link"], permalink=True)
        fe.title(post.get("title", ""))
        if post.get("description"):
            fe.description(post.get("description", ""))
        fe.pubDate(post["date"])
        if not post["existing"]:
            new_count += 1

    if new_count > 0:
        fg.rss_file(rss_file, pretty=True)
        print(f"Appended {new_count} new entry. RSS feed updated at {rss_file}")
    else:
        print("No new entries to add. RSS feed remains unchanged.")

    return posts


def get_cool_down(posts, error):
    if error:
        return 30

    if len(posts) < 2:
        return 30 * 60 # 30 mins

    MIN = 10 * 60 # 10 mins
    MAX = 60 * 60 # 1 hr

    now = datetime.now()
    post1 = datetime.strptime(posts[0]['date'], DATE_FORMAT)
    post2 = datetime.strptime(posts[1]['date'], DATE_FORMAT)
    time_between_post = (post1 - post2).total_seconds()
    if time_between_post > MAX: # disregard extra long periods
        time_between_post = 0
    last_post_time = (now - post1).total_seconds()

    return min(MAX, max(MIN, max(time_between_post, last_post_time) / 2))


def main():
    driver = initialize_browser()
    try:
        while True:
            login(driver)
            driver.get("https://wingmanreservations.com/Scheduling/Schedule.aspx")
            time.sleep(3)
            result = get_flight_status(driver)
            print(result)
            error = "error" in result
            if not error:
                flight_status = result.get("flight_status")
                hover_value = result.get("hover_tooltip_value")
                now = datetime.now().strftime(DATE_FORMAT)
                post = {"link": "https://wingmanreservations.com/Scheduling/Schedule.aspx", "date": now, "title": flight_status, "description": hover_value if hover_value else "", "existing": False}
                posts = generate_rss_feed([post])
            else:
                posts = None
            cool_down = get_cool_down(posts, error)
            print(f"Cooling down for {int(cool_down)} seconds before next fetch.")
            time.sleep(cool_down)
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
