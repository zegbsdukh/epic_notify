# -*- coding: utf-8 -*- 
import logging
import requests
import pytz
import json
import os
import html
import asyncio
from datetime import datetime, time, timezone
from dotenv import load_dotenv
# --- 兼容 Python 3.8 的类型提示 ---
from typing import List, Dict, Set, Optional, Union 

# *** 确保以下导入都正确无误 ***
# --- 兼容 python-telegram-bot v20+ ---
from telegram import Update  
from telegram.constants import ParseMode 
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    JobQueue
)
from telegram.error import TelegramError, Forbidden, BadRequest 
# ------------------------------------


# --- 配置 ---
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TARGET_TZ_NAME = os.getenv("TARGET_TIMEZONE", "Asia/Shanghai")
CHECK_HOUR = int(os.getenv("CHECK_HOUR", 23))
CHECK_MINUTE = int(os.getenv("CHECK_MINUTE", 15))
EPIC_LOCALE = os.getenv("EPIC_LOCALE", "zh-CN")
EPIC_COUNTRY = os.getenv("EPIC_COUNTRY", "CN")

if not BOT_TOKEN:
     raise ValueError("TELEGRAM_BOT_TOKEN must be set in .env file")

API_URL = f"https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale={EPIC_LOCALE}&country={EPIC_COUNTRY}&allowCountries={EPIC_COUNTRY}"
STORE_FREE_URL = f"https://store.epicgames.com/{EPIC_LOCALE}/free-games"
STORE_GAME_BASE_URL = f"https://store.epicgames.com/{EPIC_LOCALE}/p/"

SENT_GAMES_FILE = "sent_games.json"
SUBSCRIBERS_FILE = "subscribers.json" 

FALLBACK_IMAGE = "https://cdn2.unrealengine.com/epic-games-store-logo-uv-1920x1080-f43677841470.png" 

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
# 减少 requests 和 httpx 的日志噪音
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


try:
    TARGET_TZ = pytz.timezone(TARGET_TZ_NAME)
except pytz.UnknownTimeZoneError:
     logger.error(f"Unknown timezone: {TARGET_TZ_NAME}. Using UTC.")
     TARGET_TZ = pytz.UTC

# --- 状态保持 (已发送游戏 & 订阅用户) ---

def _load_json_set(filename: str) -> Set:
     if not os.path.exists(filename):
        return set()
     try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                 return set(item for item in data if item is not None)
            return set()
     except (json.JSONDecodeError, IOError):
        logger.warning(f"Could not read or parse {filename}, starting fresh.")
        return set()

def _save_json_set(filename: str, data_set: Set):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(list(item for item in data_set if item is not None), f, indent=2)
    except IOError:
         logger.error(f"Could not save state to {filename}")

def load_sent_slugs() -> Set[str]:
    return _load_json_set(SENT_GAMES_FILE)

def save_sent_slugs(slugs: Set[str]):
     _save_json_set(SENT_GAMES_FILE, slugs)

def load_subscribers() -> Set[Union[int, str]]:
     logger.debug(f"Loading subscribers from {SUBSCRIBERS_FILE}")
     return _load_json_set(SUBSCRIBERS_FILE)

def save_subscribers(subscribers: Set[Union[int, str]]):
    logger.debug(f"Saving {len(subscribers)} subscribers to {SUBSCRIBERS_FILE}")
    _save_json_set(SUBSCRIBERS_FILE, subscribers)

def add_subscriber(chat_id: Union[int, str]) -> bool:
    subs = load_subscribers()
    if chat_id in subs:
        return False
    subs.add(chat_id)
    save_subscribers(subs)
    logger.info(f"New subscriber added: {chat_id}")
    return True

def remove_subscriber(chat_id: Union[int, str]) -> bool:
     subs = load_subscribers()
     if chat_id not in subs:
         return False
     subs.remove(chat_id)
     save_subscribers(subs)
     logger.info(f"Subscriber removed: {chat_id}")
     return True

# --- 工具函数  ---
def format_date(iso_date_str: str) -> str:
    if not iso_date_str: return "N/A"
    try:
        utc_dt = datetime.fromisoformat(iso_date_str.replace("Z", "+00:00")).astimezone(timezone.utc)
        local_dt = utc_dt.astimezone(TARGET_TZ)
        return local_dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso_date_str

def get_product_slug(element: dict) -> Optional[str]:
     mappings = element.get("catalogNs", {}).get("mappings", []) or element.get("offerMappings", [])
     for mapping in mappings:
          if mapping.get("pageType") == 'productHome':
               slug = mapping.get("pageSlug")
               if slug and '/' not in slug : 
                    return slug
     slug = element.get("productSlug")
     if slug: return slug.split('/')[0] 
     slug = element.get("urlSlug")
     if slug and slug != "[]": return slug
     return None

# --- 核心抓取逻辑  ---
def get_free_games() -> List[Dict]:
    logger.info(f"Fetching data from Epic API...")
    games_list = []
    try:
        response = requests.get(API_URL, timeout=20)
        response.raise_for_status() 
        data = response.json()
        elements = data.get("data", {}).get("Catalog", {}).get("searchStore", {}).get("elements", [])

        for item in elements:
            promotions = item.get("promotions")
            if not promotions: continue
            current_offers = promotions.get("promotionalOffers")
            # 确保 current_offers 存在且其中包含实际的 promotionalOffers 列表
            if not current_offers or not any(offer_group.get("promotionalOffers") for offer_group in current_offers):
                 continue

            is_free_now, start_date_iso, end_date_iso = False, None, None
            for offer_group in current_offers:
                 for offer in offer_group.get("promotionalOffers", []):
                       if offer.get("discountSetting", {}).get("discountPercentage") == 0:
                            now_utc = datetime.now(timezone.utc)
                            try:
                                 # 确保日期字符串有效再解析
                                 start_str, end_str = offer.get("startDate"), offer.get("endDate")
                                 if not start_str or not end_str: continue
                                 start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                                 end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                                 if start_dt <= now_utc < end_dt:
                                      is_free_now, start_date_iso, end_date_iso = True, start_str, end_str
                                      break 
                            except (ValueError, TypeError): continue
                 if is_free_now: break
            
            price_info = item.get("price", {}).get("totalPrice", {})
             # 增加对 price_info 是否存在的检查
            if not price_info or not is_free_now or price_info.get("discountPrice") != 0: 
                 continue
                 
            title = item.get("title", "Unknown Title")
            image_url = FALLBACK_IMAGE
            key_images = item.get("keyImages", [])
            img_types_priority = ("OfferImageWide", "DieselStoreFrontWide", "OfferImageTall", "Thumbnail", "DieselStoreFrontTall")
            img_map = {img.get("type"): img.get("url") for img in key_images if img.get("url")}
            for img_type in img_types_priority:
                 if img_type in img_map:
                      image_url = img_map[img_type]
                      break

            product_slug = get_product_slug(item)           
            game_url = STORE_GAME_BASE_URL + product_slug if product_slug else STORE_FREE_URL
            
            # --- 确保提取 fmtPrice.originalPrice ---
            original_price_str = price_info.get("fmtPrice", {}).get("originalPrice", "N/A")
            # -----------------------------------------

            games_list.append({
                "title": title,
                "image_url": image_url,
                "start_date": format_date(start_date_iso),
                "end_date": format_date(end_date_iso),
                "url": game_url,
                 "slug": product_slug or title.replace(" ", "_"), # 优化无slug时的标识
                 "original_price": original_price_str # 使用提取的格式化字符串
            })
    except requests.exceptions.RequestException as e:
         logger.error(f"HTTP Request error: {e}")
    except json.JSONDecodeError:
        logger.error("Failed to decode JSON from API response.")
    except Exception as e:
        logger.error(f"An error occurred during fetching: {type(e).__name__} - {e}", exc_info=True) 
    return games_list

# --- Telegram Bot 函数 ---

# ######## 修改此函数 ########
async def send_game_to_chat(bot, chat_id, game: Dict, sleep_duration=1.5):
     """发送单个游戏信息到指定 chat_id"""
     
     # --- 价格显示逻辑修改开始 ---
     original_price_escaped = html.escape(str(game.get('original_price', 'N/A')))
     original_price_display = original_price_escaped
     
     # 判断：如果原价不是 "N/A" 也不是各种 "0"，才加上删除线 <s> 标签
     # 避免给 "N/A" 或 本身就是 "0" 的价格加删除线
     is_zero_price = original_price_escaped.replace(" ","").replace(".","").replace(",","").strip("¥$€") == "0"
     if original_price_escaped != 'N/A' and not is_zero_price:
          original_price_display = f"<s>{original_price_escaped}</s>"
          
     caption = (
        f"🎮 <b>{html.escape(game['title'])}</b>\n\n"
        # f"💰 原价: {html.escape(str(game['original_price']))}\n" # <-- 旧行
        #f"💰 原价: {original_price_display}\n"                  # <-- 新行1：带删除线的原价
        f"🎁 限免: <b>¥ 0.00 / 免费</b>\n"                      # <-- 新行2：限免价格
        f"⏰ 限免时间 ({TARGET_TZ_NAME}): \n"
        f"   开始: {game['start_date']}\n"
        f"   结束: {game['end_date']}\n\n"
        f"🔗 <a href='{game['url']}'>⚡️点此直接领取⚡️</a>\n"
     )
      # --- 价格显示逻辑修改结束 ---

     if len(caption) > 1024: caption = caption[:1020] + "..."

     try:
        # 确保使用 ParseMode.HTML
        await bot.send_photo(
            chat_id=chat_id, photo=game['image_url'], caption=caption, parse_mode=ParseMode.HTML
        )
     except TelegramError as e:
        logger.warning(f"Failed to send photo for {game['title']} to {chat_id}: {e}. URL: {game.get('image_url')}. Trying text only.")
        text_message = f"🖼️ 图片加载失败，但游戏信息如下：\n{caption}"
        if len(text_message) > 4096: text_message = text_message[:4090] + "..."
         # 确保使用 ParseMode.HTML
        await bot.send_message(chat_id=chat_id, text=text_message, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
     await asyncio.sleep(sleep_duration) 
# ######## 函数修改结束 ########


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not chat_id: return
    is_new = add_subscriber(chat_id)
    msg = (f"🎉 欢迎! 您已成功订阅 Epic Games Store 每周免费游戏提醒。\n" if is_new else
           f"😊 您已经订阅了 Epic Games Store 每周免费游戏提醒。\n")
    msg += (
        f"我会在发现 *新* 的免费游戏时通知您 (检查时间: {TARGET_TZ_NAME} {CHECK_HOUR:02d}:{CHECK_MINUTE:02d})。\n"
        f"使用 /checknow 手动查看当前免费游戏。\n"
        f"使用 /stop 取消订阅。"
     )
    await update.message.reply_text(msg)

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not chat_id: return
    removed = remove_subscriber(chat_id)
    msg = ("👋 已取消订阅，您将不再收到游戏推送。再次发送 /start 可重新订阅。" if removed else
           "🤔 您当前并未订阅。发送 /start 即可订阅。")
    await update.message.reply_text(msg)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

async def check_games_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("--- Starting scheduled Epic free games check & broadcast ---")
    bot = context.bot
    sent_slugs = load_sent_slugs()
    subscribers = load_subscribers() 
    current_found_slugs = set()
    games_to_send: List[Dict] = []
    ids_to_remove = set() 

    loop = asyncio.get_running_loop()
    try:
       free_games = await loop.run_in_executor(None, get_free_games)
    except Exception as e:
       logger.error(f"Error running get_free_games in executor: {e}")
       free_games = []

    if not free_games:
        logger.info("No currently free games found or API error.")
        if sent_slugs:
             logger.info("API returned no games, clearing sent_games history.")
             save_sent_slugs(set())
        return

    for game in free_games:
        current_found_slugs.add(game['slug'])
        if game['slug'] not in sent_slugs:
            games_to_send.append(game)
            logger.info(f"New game found for broadcast: {game['title']} (Slug: {game['slug']})")
   
    if games_to_send:
        header_message = f"🎉 <b>Epic 本周喜加一！</b> 🎉\n<a href='{STORE_FREE_URL}'>点击访问Epic免费游戏专区</a>\n"
        logger.info(f"Broadcasting {len(games_to_send)} new games to {len(subscribers)} subscribers...")
        
        for chat_id in subscribers:
             try:
                  await bot.send_message(chat_id=chat_id, text=header_message, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                  await asyncio.sleep(0.5) 
                  for game in games_to_send:
                       await send_game_to_chat(bot, chat_id, game, sleep_duration=1.0)
                  logger.debug(f"Successfully sent to {chat_id}")
                  await asyncio.sleep(1) 

             except (Forbidden, BadRequest) as e:
                   logger.warning(f"Cannot send to {chat_id} (Bot blocked or Chat not found: {e}). Marking for removal.")
                   ids_to_remove.add(chat_id)
             except TelegramError as e:
                  logger.error(f"Failed to send to {chat_id}: {e}")
             except Exception as e:
                   logger.error(f"Unexpected error sending to {chat_id}: {e}", exc_info=True)
        logger.info("Broadcast finished.")
    else:
         logger.info("No NEW games to broadcast.")

    if ids_to_remove:
        logger.info(f"Removing {len(ids_to_remove)} inactive/blocked chat IDs.")
        current_subs = load_subscribers() 
        updated_subs = current_subs - ids_to_remove
        if len(updated_subs) < len(current_subs):
             save_subscribers(updated_subs)

    if current_found_slugs != sent_slugs:
        logger.info(f"Updating saved slugs to: {current_found_slugs}")
        save_sent_slugs(current_found_slugs)
    else:
         logger.info("No change in the list of free games slugs since last check.")
         
    logger.info("--- Scheduled check finished ---")


async def check_now_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
     chat_id = update.effective_chat.id
     if not chat_id: return
     
     logger.info(f"Manual check requested by {chat_id}")
     reply_msg = await update.message.reply_text("收到，正在为您手动检查 Epic 当前免费游戏...") # 显示正在查询
     
     bot = context.bot
     loop = asyncio.get_running_loop()
     try:
       free_games = await loop.run_in_executor(None, get_free_games)
     except Exception as e:
        logger.error(f"Error running get_free_games in executor for manual check: {e}")
        await bot.edit_message_text(chat_id=chat_id, message_id=reply_msg.message_id, text="❌ 检查失败，请稍后再试。")
        return

     # 删除“正在查询”的消息
     try:
        await bot.delete_message(chat_id=chat_id, message_id=reply_msg.message_id)
     except TelegramError:
         pass # 忽略删除失败

     if not free_games:
          await bot.send_message(chat_id=chat_id, text="🤔 当前未找到任何限时免费游戏（或API获取失败）。")
          return
          
     header_message = f"✨ <b>Epic 当前免费游戏如下：</b> ✨\n(此为手动查询结果，非新游戏推送)\n<a href='{STORE_FREE_URL}'>点击访问Epic免费游戏专区</a>\n"
     try:
         await bot.send_message(chat_id=chat_id, text=header_message, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
         await asyncio.sleep(0.5)
         for game in free_games:
             # 使用修改后的发送函数
             await send_game_to_chat(bot, chat_id, game, sleep_duration=1.0) 
         logger.info(f"Manual check result sent to {chat_id}")
     except (Forbidden, BadRequest) as e:
           logger.warning(f"Cannot send manual result to {chat_id} (Bot blocked or Chat not found: {e}). Removing.")
           remove_subscriber(chat_id) 
     except TelegramError as e:
            logger.error(f"Failed to send manual check result to {chat_id}: {e}")
            await bot.send_message(chat_id=chat_id, text="❌ 发送游戏信息时出错。") 
     
# --- 主函数 ---
def main() -> None:
    logger.info("Starting Multi-user Epic Free Games Bot...")
    logger.info(f"Target Timezone: {TARGET_TZ_NAME}")
     
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop_command)) 
    application.add_handler(CommandHandler("checknow", check_now_command)) 
    application.add_error_handler(error_handler)

    job_queue: JobQueue = application.job_queue
    run_time = time(hour=CHECK_HOUR, minute=CHECK_MINUTE, tzinfo=TARGET_TZ)
    logger.info(f"Scheduling daily check at {run_time.strftime('%H:%M:%S %Z')}")
    
    job_queue.run_daily(
         check_games_job, 
         time=run_time, 
         name="epic_daily_check"
         )
    logger.info("Scheduling a check 8 seconds after startup.")
    job_queue.run_once(check_games_job, 8, name="startup_check") 

    # 确保程序退出时保存数据 (虽然json每次修改都保存了，但这是好习惯)
    # application.persistence = ... # 如果使用内置持久化
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Bot stopped.")

if __name__ == "__main__":
    main()