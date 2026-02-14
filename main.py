import os
import pickle
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

from config import cookies

load_dotenv('./.env')
env = dotenv_values()


USE_SAFARI = False  


profiles = env['PROFILES'].split(',')  
COOKIES_FILE = env['COOKIES_PATH']
RSS_OUTPUT_DIR = env['RSS_PATH']


TWEETS_PER_PROFILE = 10


MAX_SCROLL_ATTEMPTS = 10


AFTER_PROFILE_MIN_WAIT = 30  
AFTER_PROFILE_MAX_WAIT = 60  

DATE_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

TWEET_SELECTORS = [
    "article[role='article']",
    "div[data-testid='tweet']",
]


def random_sleep(min_sec=2, max_sec=5):
    """Sleep for a random time between min_sec and max_sec."""
    import math
    sleep_time = random.uniform(min_sec, max_sec)
    print(f"Sleeping for ~{math.ceil(sleep_time)} seconds...")
    time.sleep(sleep_time)

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

def save_cookies(driver):
    with open(COOKIES_FILE, "wb") as f:
        pickle.dump(driver.get_cookies(), f)
    print("Cookies saved to file.")

def load_cookies(driver):
    """Load cookies from file, if they exist, to avoid re-login."""
    if os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE, "rb") as f:
            cookies = pickle.load(f)
        driver.get("https://x.com")
        for cookie in cookies:

            if "domain" in cookie and cookie["domain"].startswith("."):
                cookie["domain"] = "x.com"
            driver.add_cookie(cookie)
        print("Cookies loaded.")
        return True
    return False

def login_to_x(driver):
    """Perform login if cookies are missing or invalid."""
    driver.get("https://x.com")
    for key, value in cookies.items():
        driver.add_cookie({"name": key, "value": value})
    print("Cookies initialized.")

def navigate_to_profile(driver, profile):
    """Go to the X/Twitter profile page."""
    profile_url = f"https://x.com/{profile}"
    driver.get(profile_url)
    driver.save_screenshot('screenshot/1.png')
    random_sleep(4, 7)
    print(f"Navigated to profile: {profile_url}")

def find_element_with_multiple_selectors(parent, selector_list):
    """
    Attempt to find an element using each CSS selector in `selector_list`.
    Return the first element that matches, or None if none match.
    """
    from selenium.common.exceptions import NoSuchElementException
    for selector in selector_list:
        try:
            return parent.find_element(By.CSS_SELECTOR, selector)
        except NoSuchElementException:
            pass
    return None

def find_tweet_elements(driver):
    """
    Return a combined list of unique tweet elements found
    using multiple tweet selectors.
    """
    all_tweets = []
    for sel in TWEET_SELECTORS:
        found = driver.find_elements(By.CSS_SELECTOR, sel)
        all_tweets.extend(found)
    
    return list(set(all_tweets))

def sort_posts(posts):
    return sorted(posts, key=lambda x: datetime.strptime(x['date'], DATE_FORMAT), reverse=True)

def gather_latest_posts(driver, profile, n):
    """
    Gather up to n of the latest tweets from the user's timeline, 
    scrolling as needed. Returns a list of dicts:
    [{"link": <tweet_url>, "date": <ISO-8601 datetime>}...]
    """
    collected_posts = []
    seen_links = set()
    scroll_attempts = 0

    while len(collected_posts) < n and scroll_attempts < MAX_SCROLL_ATTEMPTS:
        
        tweets = find_tweet_elements(driver)
        print(f"DEBUG: Found {len(tweets)} tweet elements in the DOM (raw).")

        
        new_count_this_round = 0
        for tweet in tweets:
            
            tweet_link_element = find_element_with_multiple_selectors(tweet, [
                f"a[href*='/{profile}/status/']",
                "time ~ a[href*='/status/']",
            ])
            if not tweet_link_element:
                
                continue

            link = tweet_link_element.get_attribute("href")
            if link in seen_links:
                continue  

            tweet_time_element = find_element_with_multiple_selectors(tweet, [
                "time",
                "a time",
            ])
            if not tweet_time_element:
                continue

            date = tweet_time_element.get_attribute("datetime")

            collected_posts.append({"existing": False, "link": link, "date": date})
            seen_links.add(link)
            new_count_this_round += 1

        print(f"Collected {new_count_this_round} new tweets in this round. Total so far: {len(collected_posts)}")

        
        if len(collected_posts) < n:
            old_total = len(collected_posts)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
            random_sleep(3, 6)

            
            if len(collected_posts) == old_total:
                scroll_attempts += 1
                print(f"No new tweets found after scrolling. Scroll attempts: {scroll_attempts}")
        else:
            
            break

    print(f"Found {len(collected_posts)} total tweets for {profile} after {scroll_attempts} scroll attempts.")
    return sort_posts(collected_posts)[:n]

def fetch_embed_codes(profile, driver, posts):
    """
    Open a second tab to get each tweet's embed code from publish.twitter.com
    without leaving the profile page in the main tab. 
    Modifies `posts` in place by adding an "embed" key.
    """
    if not posts:
        return posts, True

    error = False
    os.makedirs(RSS_OUTPUT_DIR, exist_ok=True)
    rss_file = os.path.join(RSS_OUTPUT_DIR, f"{profile}.xml")

    _, existing_entries = load_existing_feed_entries(rss_file)

    
    driver.execute_script("window.open('about:blank','_blank');")
    time.sleep(1)

    main_tab = driver.window_handles[0]
    embed_tab = driver.window_handles[1]

    pattern = '<p .*?>(.*?)<\/p>'
    
    for idx, post in enumerate(posts, start=1):
        if post['link'] in existing_entries:
            print(f"Skipping existing tweet (already in RSS): {post['link']}")
            continue
        
        tweet_link = post["link"]
        embed_url = f"https://publish.twitter.com/?query={tweet_link}&widget=Tweet"

        driver.switch_to.window(embed_tab)
        driver.get(embed_url)
        random_sleep(3, 5)

        try:
            textarea = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "textarea"))
            )
            embed_code = textarea.get_attribute("value")
            embed = re.search(pattern, embed_code).group(1)
            embed = re.sub(r'<a[^>]*>(.*?)</a>', r'\1', embed)
            embed = re.sub(r'<br\s*/?>', '\n', embed)
            post["embed"] = embed
            print(f"Fetched embed")
        except Exception as e:
            print(f"Failed to get embed code for {tweet_link}: {e}")
            error = True
            post["embed"] = None

    
    driver.switch_to.window(main_tab)
    return posts, error

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
            old_posts.append({"embed": entry.title, "existing": True, "link": guid_val, "date": datetime(*entry.published_parsed[:6]).strftime(DATE_FORMAT)})
    return old_posts, existing_entries

def generate_rss_feed(profile, posts):
    """
    1) Load any existing RSS file and re-add old entries (so we keep them).
    2) Append new tweets if not already present by GUID (the tweet link).
    3) Save updated feed to the same file.
    """
    os.makedirs(RSS_OUTPUT_DIR, exist_ok=True)
    rss_file = os.path.join(RSS_OUTPUT_DIR, f"{profile}.xml")

    old_posts, existing_entries = load_existing_feed_entries(rss_file)
    posts = list(filter(lambda x: x['link'] not in existing_entries, posts))
    posts = sort_posts(posts + old_posts)[:TWEETS_PER_PROFILE]

    fg = FeedGenerator()
    fg.title(f"{profile}".capitalize())
    fg.link(href=f"https://xcancel.com/{profile}", rel="alternate")
    fg.description(f"RSS feed of the latest tweets from {profile}.")
    fg.language("en")

    
    new_count = 0
    for post in posts:
        link = post["link"]

        embed_code = post.get("embed", "")
        if not embed_code:
            continue
        
        fe = fg.add_entry()
        fe.link(href=link.replace("x.com", "xcancel.com"), rel="alternate")
        fe.guid(link, permalink=True)
        fe.title(embed_code if embed_code else "No embed code available")
        fe.pubDate(post["date"])

        if not post["existing"]:
            new_count += 1

    if new_count > 0:
        fg.rss_file(rss_file, pretty=True)
        print(f"Appended {new_count} new tweet(s). RSS feed updated at {rss_file}")
    else:
        print("No new tweets to add. RSS feed remains unchanged.")

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
            if not load_cookies(driver):
                login_to_x(driver)

            for profile in profiles:
                
                navigate_to_profile(driver, profile)

                
                posts = gather_latest_posts(driver, profile, TWEETS_PER_PROFILE)

                
                posts, error = fetch_embed_codes(profile, driver, posts)

                
                posts = generate_rss_feed(profile, posts)

                
                # cool_down = random.uniform(AFTER_PROFILE_MIN_WAIT, AFTER_PROFILE_MAX_WAIT)
                # print(f"Cooling down for {int(cool_down)} seconds before next profile.")
                # time.sleep(cool_down)
            save_cookies(driver)
            cool_down = get_cool_down(posts, error)
            print(f"Cooling down for {int(cool_down)} seconds before next fetch.")
            time.sleep(cool_down)

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
