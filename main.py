import os
import json
import threading
import time
from datetime import datetime
from flask import Flask, request, abort
import requests
from bs4 import BeautifulSoup
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler
import schedule

app = Flask(__name__)

BOT_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

if not BOT_TOKEN or not CHANNEL_ID:
    raise RuntimeError("TELEGRAM_TOKEN and CHANNEL_ID must be set in environment variables")

bot = Bot(token=BOT_TOKEN)
JSON_FILE = 'seen_posts.json'
URL = 'https://it.tlscontact.com/by/MSQ/page.php?pid=news'

last_check_time = None

def parse_date(date_str):
    clean_date = date_str.replace(' ', '').lower()
    if clean_date in ['datenotfound', '', 'datenotfound']:
        return None
    formats = ['%d/%m/%Y', '%m/%d/%Y']
    for fmt in formats:
        try:
            return datetime.strptime(clean_date, fmt)
        except ValueError:
            continue
    print(f"[ERROR init send] time data '{date_str}' does not match known formats")
    return None

def load_seen_posts():
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if data and isinstance(data[0], str):
                return [{'id': x, 'title': '', 'date': '', 'description': ''} for x in data]
            return data
    return []

def save_seen_posts(posts):
    posts_with_dates = [p for p in posts if parse_date(p['date']) is not None]
    posts_without_dates = [p for p in posts if parse_date(p['date']) is None]
    posts_with_dates.sort(key=lambda p: parse_date(p['date']), reverse=True)
    sorted_posts = posts_with_dates + posts_without_dates
    with open(JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(sorted_posts, f, ensure_ascii=False, indent=2)

seen_posts = load_seen_posts()
seen_ids = set(p['id'] for p in seen_posts)

def fetch_news(send_last_only=False):
    global seen_posts, seen_ids, last_check_time
    try:
        print("Checking news...")
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        headings = soup.find_all('h3', class_='mb-0')
        if not headings:
            print("No news found on the page.")
            return

        all_news = []
        for h3 in headings:
            title = h3.get_text(strip=True)
            parent_div = h3.find_parent('div', class_='d-flex')
            if not parent_div:
                continue
            date_p = parent_div.find_next_sibling('p')
            date = date_p.find('strong').get_text(strip=True) if date_p and date_p.find('strong') else ''
            desc_p = date_p.find_next_sibling('p') if date_p else None
            description = desc_p.get_text(strip=True) if desc_p else ''
            unique_id = f"{title}||{date}"
            all_news.append({'id': unique_id, 'title': title, 'date': date, 'description': description})

        valid_news = [n for n in all_news if parse_date(n['date']) is not None]
        invalid_news = [n for n in all_news if parse_date(n['date']) is None]
        valid_news.sort(key=lambda n: parse_date(n['date']), reverse=True)
        all_news = valid_news + invalid_news

        last_check_time = datetime.now()

        if send_last_only:
            if not seen_posts:
                seen_posts = all_news.copy()
                seen_ids = set(p['id'] for p in seen_posts)
                save_seen_posts(seen_posts)
                print("Initialized seen_posts with existing news.")
            if all_news:
                last = all_news[0]
                msg = f"*{last['title']}*\n_{last['date']}_\n\n{last['description']}"
                bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')
                print("Last news sent at startup.")
            return

        new_found = False
        for news in all_news:
            if news['id'] not in seen_ids:
                seen_ids.add(news['id'])
                seen_posts.append(news)
                msg = f"*{news['title']}*\n_{news['date']}_\n\n{news['description']}"
                bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')
                print(f"Sent new news: {news['title']}")
                new_found = True

        if new_found:
            save_seen_posts(seen_posts)
        else:
            print("No new news found.")

    except Exception as e:
        print(f"[Error] Cannot get news: {e}")

def check_command(update, context):
    update.message.reply_text("Bot is running")

def lastnew_command(update, context):
    if seen_posts:
        last = seen_posts[-1]
        date_str = last_check_time.strftime("%Y-%m-%d %H:%M:%S") if last_check_time else "unknown"
        msg = (
            f"*Last news:*\n"
            f"{last['title']}\n"
            f"News date: {last['date']}\n\n"
            f"Last check time: {date_str}"
        )
        update.message.reply_text(msg, parse_mode='Markdown')
    else:
        update.message.reply_text("No news available yet")

from telegram.ext import Dispatcher

dispatcher = Dispatcher(bot, None, workers=0)
dispatcher.add_handler(CommandHandler("check", check_command))
dispatcher.add_handler(CommandHandler("lastnew", lastnew_command))

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
        return 'OK'
    else:
        abort(403)

def run_schedule():
    schedule.every(5).minutes.do(fetch_news)
    fetch_news(send_last_only=True)
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    thread = threading.Thread(target=run_schedule, daemon=True)
    thread.start()
    port = int(os.getenv('PORT', 5000))
    print(f"[INFO] Flask запускается на порту {port}")
    app.run(host='0.0.0.0', port=port)

