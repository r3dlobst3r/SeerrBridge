# =============================================================================
# Soluify.com  |  Your #1 IT Problem Solver  |  {SeerrBridge v0.3.4}
# =============================================================================
#  __         _
# (_  _ |   .(_
# __)(_)||_||| \/
#              /
# © 2024
# -----------------------------------------------------------------------------
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel, Field, ValidationError, field_validator, ConfigDict
from typing import Optional, List, Dict, Any, Union
import asyncio
import json
import time
import os
import sys
import urllib.parse
import re
import inflect
import requests
import platform
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException, TimeoutException
from asyncio import Queue
from datetime import datetime, timedelta
from deep_translator import GoogleTranslator
from fuzzywuzzy import fuzz
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from selenium.webdriver.common.keys import Keys
import aiohttp
from types import SimpleNamespace


# Configure loguru
logger.remove()  # Remove default handler
logger.add("seerbridge.log", rotation="500 MB", encoding='utf-8')  # Use utf-8 encoding for log file
logger.add(sys.stdout, colorize=True)  # Ensure stdout can handle Unicode
logger.level("WARNING", color="<cyan>")

# Load environment variables
load_dotenv()

# Initialize FastAPI
app = FastAPI()

# Securely load credentials from environment variables
RD_ACCESS_TOKEN = os.getenv('RD_ACCESS_TOKEN')
RD_REFRESH_TOKEN = os.getenv('RD_REFRESH_TOKEN')
RD_CLIENT_ID = os.getenv('RD_CLIENT_ID')
RD_CLIENT_SECRET = os.getenv('RD_CLIENT_SECRET')
OVERSEERR_BASE = os.getenv('OVERSEERR_BASE')
OVERSEERR_API_BASE_URL = f"{OVERSEERR_BASE}/api/v1"
OVERSEERR_API_KEY = os.getenv('OVERSEERR_API_KEY')
TMDB_API_KEY = os.getenv('TMDB_API_KEY')
HEADLESS_MODE = os.getenv("HEADLESS_MODE", "true").lower() == "true"
ENABLE_AUTOMATIC_BACKGROUND_TASK = os.getenv("ENABLE_AUTOMATIC_BACKGROUND_TASK", "false").lower() == "true"
TORRENT_FILTER_REGEX = os.getenv("TORRENT_FILTER_REGEX")

# Confirm the interval is a valid number.
try:
    REFRESH_INTERVAL_MINUTES = float(os.getenv("REFRESH_INTERVAL_MINUTES"))
except (TypeError, ValueError):
    logger.error("REFRESH_INTERVAL_MINUTES environment variable is not a valid number.")
    exit(1)

if not OVERSEERR_API_BASE_URL:
    logger.error("OVERSEERR_API_BASE_URL environment variable is not set.")
    exit(1)

if not OVERSEERR_API_KEY:
    logger.error("OVERSEERR_API_KEY environment variable is not set.")
    exit(1)

if not TMDB_API_KEY:
    logger.error("TMDB_API_KEY environment variable is not set.")
    exit(1)

# Global driver variable to hold the Selenium WebDriver
driver = None

# Initialize a global queue with a maximum size of 500
request_queue = Queue(maxsize=500)
processing_task = None  # To track the current processing task

class MediaInfo(BaseModel):
    media_type: str
    tmdbId: str
    tvdbId: Optional[str] = None
    status: str

class RequestInfo(BaseModel):
    request_id: str
    requestedBy_email: Optional[str] = None
    requestedBy_username: Optional[str] = None
    requestedBy_avatar: Optional[str] = None

class ExtraInfo(BaseModel):
    name: str
    value: str

class WebhookPayload(BaseModel):
    media: MediaInfo
    request: RequestInfo
    extra: Optional[List[ExtraInfo]] = None

def refresh_access_token():
    global RD_REFRESH_TOKEN, RD_ACCESS_TOKEN, driver

    TOKEN_URL = "https://api.real-debrid.com/oauth/v2/token"
    data = {
        'client_id': RD_CLIENT_ID,
        'client_secret': RD_CLIENT_SECRET,
        'code': RD_REFRESH_TOKEN,
        'grant_type': 'http://oauth.net/grant_type/device/1.0'
    }
    try:
        logger.info("Requesting a new access token with the refresh token.")
        response = requests.post(TOKEN_URL, data=data)
        response.encoding = 'utf-8'  # Explicitly set UTF-8 encoding for the response
        response_data = response.json()

        if response.status_code == 200:
            expiry_time = int((datetime.now() + timedelta(hours=24)).timestamp() * 1000)
            RD_ACCESS_TOKEN = json.dumps({
                "value": response_data['access_token'],
                "expiry": expiry_time
            }, ensure_ascii=False)  # Ensure non-ASCII characters are preserved
            logger.success("Successfully refreshed access token.")
            
            update_env_file()

            if driver:
                driver.execute_script(f"""
                    localStorage.setItem('rd:accessToken', '{RD_ACCESS_TOKEN}');
                """)
                logger.info("Updated Real-Debrid credentials in local storage after token refresh.")
                driver.refresh()
                logger.success("Refreshed the page after updating local storage with the new token.")
        else:
            logger.error(f"Failed to refresh access token: {response_data.get('error_description', 'Unknown error')}")
    except Exception as e:
        logger.error(f"Error refreshing access token: {e}")

def update_env_file():
    """Update the .env file with the new access token."""
    with open('.env', 'r', encoding='utf-8') as file:
        lines = file.readlines()

    with open('.env', 'w', encoding='utf-8') as file:
        for line in lines:
            if line.startswith('RD_ACCESS_TOKEN'):
                file.write(f'RD_ACCESS_TOKEN={RD_ACCESS_TOKEN}\n')
            else:
                file.write(line)


def check_and_refresh_access_token():
    """Check if the access token is expired or about to expire and refresh it if necessary."""
    global RD_ACCESS_TOKEN
    RD_ACCESS_TOKEN = None  # Reset before reloading
    load_dotenv(override=True)
    RD_ACCESS_TOKEN = os.getenv('RD_ACCESS_TOKEN')
    if RD_ACCESS_TOKEN:
        token_data = json.loads(RD_ACCESS_TOKEN)
        expiry_time = token_data['expiry']  # This is in milliseconds
        current_time = int(time.time() * 1000)  # Convert current time to milliseconds

        # Convert expiry time to a readable date format
        expiry_date = datetime.fromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')

        # Print the expiry date
        logger.info(f"Access token will expire on: {expiry_date}")

        # Check if the token is about to expire in the next 10 minutes (600000 milliseconds)
        if current_time >= expiry_time - 600000:  # 600000 milliseconds = 10 minutes
            logger.info("Access token is about to expire. Refreshing...")
            refresh_access_token()
        else:
            logger.info("Access token is still valid.")
    else:
        logger.error("Access token is not set. Requesting a new token.")
        refresh_access_token()

### Helper function to handle login
def login(driver):
    logger.info("Initiating login process.")

    try:
        # Check if the "Login with Real Debrid" button exists and is clickable
        login_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Login with Real Debrid')]"))
        )
        if login_button:
            login_button.click()
            logger.info("Clicked on 'Login with Real Debrid' button.")
        else:
            logger.info("'Login with Real Debrid' button was not found. Skipping this step.")

    except TimeoutException:
        # Handle case where the button was not found before the timeout
        logger.warning("'Login with Real Debrid' button not found or already bypassed. Proceeding...")
    
    except NoSuchElementException:
        # Handle case where the element is not in the DOM
        logger.warning("'Login with Real Debrid' button not present in the DOM. Proceeding...")

    except Exception as ex:
        # Log any other unexpected exception
        logger.error(f"An unexpected error occurred during login: {ex}")


scheduler = AsyncIOScheduler()

### Browser Initialization and Persistent Session
async def initialize_browser():
    global driver
    if driver is None:
        logger.info("Starting persistent browser session.")

        # Detect the current operating system
        current_os = platform.system().lower()  # Returns 'windows', 'linux', or 'darwin' (macOS)
        logger.info(f"Detected operating system: {current_os}")

        options = Options()

        ### Handle Docker/Linux-specific configurations
        if current_os == "linux" and os.getenv("RUNNING_IN_DOCKER", "false").lower() == "true":
            logger.info("Detected Linux environment inside Docker. Applying Linux-specific configurations.")

            # Explicitly set the Chrome binary location
            options.binary_location = os.getenv("CHROME_BIN", "/usr/bin/google-chrome")

            # Enable headless mode for Linux/Docker environments
            options.add_argument("--headless=new")  # Updated modern headless flag
            options.add_argument("--no-sandbox")  # Required for running as root in Docker
            options.add_argument("--disable-dev-shm-usage")  # Handle shared memory limitations
            options.add_argument("--disable-gpu")  # Disable GPU rendering for headless environments
            options.add_argument("--disable-setuid-sandbox")  # Bypass setuid sandbox

        ### Handle Windows-specific configurations
        elif current_os == "windows":
            logger.info("Detected Windows environment. Applying Windows-specific configurations.")

        if HEADLESS_MODE:
            options.add_argument("--headless=new")  # Modern headless mode for Chrome
        options.add_argument("--disable-gpu")  # Disable GPU for Docker compatibility
        options.add_argument("--no-sandbox")  # Required for running browser as root
        options.add_argument("--disable-dev-shm-usage")  # Disable shared memory usage restrictions
        options.add_argument("--disable-setuid-sandbox")  # Disable sandboxing for root permissions
        options.add_argument("--enable-logging")
        options.add_argument("--window-size=1920,1080")  # Set explicit window size to avoid rendering issues

        # WebDriver options to suppress infobars and disable automation detection
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-infobars")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36")


        # Log initialization method
        logger.info("Using WebDriver Manager for dynamic ChromeDriver downloads.")

        try:
            # Use webdriver-manager to install the appropriate ChromeDriver version
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

            # Suppress 'webdriver' detection
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": """
                Object.defineProperty(navigator, 'webdriver', {
                  get: () => undefined
                })
                """
            })

            logger.success("Initialized Selenium WebDriver with WebDriver Manager.")
            # Navigate to an initial page to confirm browser works
            driver.get("https://debridmediamanager.com")
            logger.success("Navigated to Debrid Media Manager page.")
        except Exception as e:
            logger.error(f"Failed to initialize Selenium WebDriver: {e}")
            raise e

        # Inject Real-Debrid access token and other credentials into local storage
        driver.execute_script(f"""
            localStorage.setItem('rd:accessToken', '{RD_ACCESS_TOKEN}');
        """)
        logger.info("Set Real-Debrid credentials in local storage.")

        # Refresh the page to apply the local storage values
        driver.refresh()
        login(driver)
        logger.success("Refreshed the page to apply local storage values.")
        # After refreshing, call the login function to click the login button
        # After successful login, click on "⚙️ Settings" to open the settings popup
        try:

            logger.info("Attempting to click the '⚙️ Settings' link.")
            settings_link = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//span[contains(text(),'⚙️ Settings')]"))
            )
            settings_link.click()
            logger.info("Clicked on '⚙️ Settings' link.")

            # Locate the "Default torrents filter" input box and insert the regex
            logger.info("Attempting to insert regex into 'Default torrents filter' box.")
            default_filter_input = WebDriverWait(driver, 10).until(

                EC.presence_of_element_located((By.ID, "dmm-default-torrents-filter"))
            )
            default_filter_input.clear()  # Clear any existing filter

            # Use the regex from .env
            default_filter_input.send_keys(TORRENT_FILTER_REGEX)

            logger.info(f"Inserted regex into 'Default torrents filter' input box: {TORRENT_FILTER_REGEX}")

            settings_link.click()
            logger.success("Closed 'Settings' to save settings.")

        except (TimeoutException, NoSuchElementException) as ex:
            logger.error(f"Error while interacting with the settings: {ex}")
            logger.error(f"Continuing without TORRENT_FILTER_REGEX")


        # Navigate to the library section
        logger.info("Navigating to the library section.")
        driver.get("https://debridmediamanager.com/library")

        # Wait for 2 seconds on the library page before further processing
        try:
            # Ensure the library page has loaded correctly (e.g., wait for a specific element on the library page)
            library_element = WebDriverWait(driver, 2).until(
                EC.presence_of_element_located((By.XPATH, "//div[@id='library-content']"))  # Adjust the XPath as necessary
            )
            logger.success("Library section loaded successfully.")
        except TimeoutException:
            logger.info("Library loading.")

        # Wait for at least 2 seconds on the library page
        logger.info("Waiting for 2 seconds on the library page.")
        time.sleep(2)
        logger.success("Completed waiting on the library page.")

async def shutdown_browser():
    global driver
    if driver:
        driver.quit()
        logger.warning("Selenium WebDriver closed.")
        driver = None

### Function to process requests from the queue
async def process_requests():
    """Process both movie and TV show requests"""
    requests = get_overseerr_media_requests()
    if not requests:
        logger.info("No requests to process")
        return
    
    for request in requests:
        try:
            tmdb_id = request['media']['tmdbId']
            media_id = request['media']['id']
            media_type = request['media'].get('mediaType', '').lower()
            
            logger.info(f"Processing request with TMDB ID {tmdb_id} and media ID {media_id} of type {media_type}")
            
            if media_type == 'tv':
                # Create properly structured payload for TV shows
                payload = WebhookPayload(
                    media=MediaInfo(
                        media_type='tv',
                        tmdbId=str(tmdb_id),
                        tvdbId=str(request['media'].get('tvdbId')),
                        status='PENDING'
                    ),
                    request=RequestInfo(
                        request_id=str(request.get('id')),
                        requestedBy_email=request.get('requestedBy', {}).get('email'),
                        requestedBy_username=request.get('requestedBy', {}).get('username'),
                        requestedBy_avatar=request.get('requestedBy', {}).get('avatar')
                    ),
                    extra=[
                        ExtraInfo(
                            name='Requested Seasons',
                            value=', '.join(str(s['seasonNumber']) for s in request.get('seasons', []))
                        )
                    ] if request.get('seasons') else None
                )
                await process_tv_request(payload)
            else:
                # Handle as movie (existing logic)
                movie_details = get_movie_details_from_tmdb(tmdb_id)
                if not movie_details:
                    logger.error(f"Failed to get movie details for TMDB ID {tmdb_id}")
                    continue
                
                movie_title = f"{movie_details['title']} ({movie_details['year']})"
                logger.info(f"Processing movie request: {movie_title}")
                
                try:
                    confirmation_flag = await asyncio.to_thread(search_on_debrid, movie_title, driver)
                    if confirmation_flag:
                        if mark_completed(media_id, tmdb_id):
                            logger.success(f"Marked media {media_id} as completed in overseerr")
                        else:
                            logger.error(f"Failed to mark media {media_id} as completed in overseerr")
                    else:
                        logger.info(f"Media {media_id} was not properly confirmed. Skipping marking as completed.")
                except Exception as ex:
                    logger.critical(f"Error processing movie request {movie_title}: {ex}")
                    
        except Exception as e:
            logger.error(f"Error processing request: {e}")
            continue

    logger.info("Finished processing all current requests. Waiting for new requests.")

### Function to add requests to the queue
async def add_request_to_queue(movie_title):
    if request_queue.full():
        logger.warning(f"Request queue is full. Cannot add movie: {movie_title}")
        return False
    
    await request_queue.put(movie_title)
    logger.info(f"Added movie request to queue: {movie_title}")
    return True

### Helper function to extract year from a string
def extract_year(text, ignore_resolution=False):
    if ignore_resolution:
        # Remove resolution strings like "2160p"
        text = re.sub(r'\b\d{3,4}p\b', '', text)
    
    # Extract the year using a regular expression
    match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    if match:
        return int(match.group(0))
    return None


# Initialize the inflect engine for number-word conversion
p = inflect.engine()

def translate_title(title, target_lang='en'):
    """
    Detects the language of the input title and translates it to the target language.
    """
    try:
        translator = GoogleTranslator(source='auto', target=target_lang)
        translated_title = translator.translate(title)
        logger.info(f"Translated '{title}' to '{translated_title}'")
        return translated_title
    except Exception as e:
        logger.error(f"Error translating title '{title}': {e}")
        return title  # Return the original title if translation fails


def clean_title(title, target_lang='en'):
    """
    Cleans the movie title by removing commas, hyphens, colons, semicolons, and apostrophes,
    translating it to the target language, and converting to lowercase.
    """
    # Translate the title to the target language
    translated_title = translate_title(title, target_lang)

    # Remove commas, hyphens, colons, semicolons, and apostrophes
    cleaned_title = re.sub(r"[,:;'-]", '', translated_title)
    # Replace multiple spaces with a single dot
    cleaned_title = re.sub(r'\s+', '.', cleaned_title)
    # Convert to lowercase for comparison
    return cleaned_title.lower()

def normalize_title(title, target_lang='en'):
    """
    Normalizes the title by ensuring there are no unnecessary spaces or dots,
    translating it to the target language, and converting to lowercase.
    """
    # Replace ellipsis with three periods
    title = title.replace('…', '...')
    # Replace smart apostrophes with regular apostrophes
    title = title.replace('’', "'")
    # Further normalization can be added here if required
    return title.strip()
    # Translate the title to the target language
    translated_title = translate_title(title, target_lang)

    # Replace multiple spaces with a single space and dots with spaces
    normalized_title = re.sub(r'\s+', ' ', translated_title)
    normalized_title = normalized_title.replace('.', ' ')
    # Convert to lowercase
    return normalized_title.lower()


def replace_numbers_with_words(title):
    """
    Replaces digits with their word equivalents (e.g., "3" to "three").
    """
    return re.sub(r'\b\d+\b', lambda x: p.number_to_words(x.group()), title)

def replace_words_with_numbers(title):
    """
    Replaces number words with their digit equivalents (e.g., "three" to "3").
    """
    words_to_numbers = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
        "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
        "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
        "eighteen": "18", "nineteen": "19", "twenty": "20"
        # Add more mappings as needed
    }

    # Replace word numbers with digits
    for word, digit in words_to_numbers.items():
        title = re.sub(rf'\b{word}\b', digit, title, flags=re.IGNORECASE)
    return title


# Function to fetch media requests from Overseerr
def get_overseerr_media_requests() -> list[dict]:
    url = f"{OVERSEERR_API_BASE_URL}/request?take=500&filter=approved&sort=added"
    headers = {
        "X-Api-Key": OVERSEERR_API_KEY
    }
    response = requests.get(url, headers=headers)
    
    if response.status_code != 200:
        logger.error(f"Failed to fetch requests from Overseerr: {response.status_code}")
        return []
    
    data = response.json()
    logger.info(f"Fetched {len(data.get('results', []))} requests from Overseerr")
    
    if not data.get('results'):
        return []
    
    # Filter requests that are in processing state (status 3)
    processing_requests = [item for item in data['results'] if item['status'] == 2 and item['media']['status'] == 3]
    logger.info(f"Filtered {len(processing_requests)} processing requests")
    return processing_requests

# Trakt API rate limit: 1000 calls every 5 minutes
TRAKT_RATE_LIMIT = 1000
TRAKT_RATE_LIMIT_PERIOD = 5 * 60  # 5 minutes in seconds

trakt_api_calls = 0
last_reset_time = time.time()

def get_movie_details_from_tmdb(tmdb_id: str) -> Optional[dict]:
    """Fetch movie details directly from TMDB API"""
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
    params = {
        "api_key": os.getenv('TMDB_API_KEY')
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return {
                "title": data['title'],
                "year": datetime.strptime(data['release_date'], '%Y-%m-%d').year if data.get('release_date') else None
            }
        else:
            logger.error(f"TMDB API request failed with status code {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error fetching movie details from TMDB API: {e}")
        return None

async def process_movie_request(payload: WebhookPayload):
    try:
        # Extract TMDB ID and request_id from the payload
        tmdb_id = payload.media.tmdbId
        request_id = payload.request.request_id if hasattr(payload, 'request') else None
            
        logger.info(f"Processing request for TMDB ID {tmdb_id} with request_id {request_id}")
        
        # Get movie details from TMDB using the synchronous function
        movie_details = get_movie_details_from_tmdb(tmdb_id)
        if not movie_details:
            logger.error(f"Failed to fetch movie details for TMDB ID {tmdb_id}")
            return {"status": "error", "message": "Failed to fetch movie details"}

        # Format movie title with year
        movie_title = f"{movie_details['title']} ({movie_details['year']})"
        logger.info(f"Processing movie request: {movie_title}")
        
        try:
            # Add timeout handling for search_on_debrid
            confirmation_flag = await asyncio.wait_for(
                asyncio.to_thread(search_on_debrid, movie_title, driver),
                timeout=60.0  # 60 second timeout
            )
            
            if confirmation_flag and request_id:
                # Get the media_id using the request_id
                media_id = get_media_id_from_request(request_id)
                if media_id:
                    if mark_completed(media_id, tmdb_id):
                        logger.success(f"Successfully marked media {media_id} as completed in overseerr")
                    else:
                        logger.error(f"Failed to mark media {media_id} as completed in overseerr")
                else:
                    logger.error(f"Could not find media_id for request_id {request_id}")
            else:
                logger.warning(f"Request {request_id} was not properly confirmed. Skipping marking as completed.")
                
        except asyncio.TimeoutError:
            logger.error(f"Timeout while processing movie request {movie_title}")
            return {"status": "error", "message": "Request processing timed out"}
        except Exception as ex:
            logger.critical(f"Error processing movie request {movie_title}: {ex}")
                
        return {
            "status": "success", 
            "tmdb_id": tmdb_id,
            "request_id": request_id,
            "title": movie_title,
            "message": "Request processed"
        }
        
    except Exception as e:
        logger.error(f"Error processing movie request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def get_media_id_from_request(request_id: str) -> Optional[int]:
    """Get the media_id from Overseerr using the request_id"""
    url = f"{OVERSEERR_API_BASE_URL}/request/{request_id}"
    headers = {
        "X-Api-Key": OVERSEERR_API_KEY
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get('media', {}).get('id')
        else:
            logger.error(f"Failed to get request details: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error getting request details: {e}")
        return None

def mark_completed(media_id: int, tmdb_id: int) -> bool:
    """Mark item as completed in overseerr"""
    url = f"{OVERSEERR_API_BASE_URL}/media/{media_id}/available"
    headers = {
        "X-Api-Key": OVERSEERR_API_KEY,
        "Content-Type": "application/json"
    }
    data = {"is4k": False}
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response_data = response.json()  # Parse the JSON response
        
        if response.status_code == 200:
            # Verify that the response contains the correct tmdb_id
            if response_data.get('tmdbId') == tmdb_id:
                logger.info(f"Marked media {media_id} as completed in overseerr. Response: {response_data}")
                return True
            else:
                logger.error(f"TMDB ID mismatch for media {media_id}. Expected {tmdb_id}, got {response_data.get('tmdbId')}")
                return False
        else:
            logger.error(f"Failed to mark media as completed in overseerr with id {media_id}: Status code {response.status_code}, Response: {response_data}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to mark media as completed in overseerr with id {media_id}: {str(e)}")
        return False
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON response for media {media_id}: {str(e)}")
        return False


def prioritize_buttons_in_box(result_box):
    """
    Prioritize buttons within a result box. Clicks the 'Instant RD' or 'DL with RD' button
    if available.
    """
    try:
        # First try to find and click 'Instant RD' button
        instant_rd_buttons = result_box.find_elements(By.CSS_SELECTOR, "button[title='Instant RD']")
        if instant_rd_buttons:
            instant_rd_buttons[0].click()
            time.sleep(1)  # Wait for click to register
            return True
            
        # If no 'Instant RD' button, try 'DL with RD' button
        dl_buttons = result_box.find_elements(By.CSS_SELECTOR, "button[title='DL with RD']")
        if dl_buttons:
            dl_buttons[0].click()
            time.sleep(1)  # Wait for click to register
            return True
            
        return False
        
    except Exception as e:
        logger.error(f"Error in prioritize_buttons_in_box: {e}")
        return False




### Search Function to Reuse Browser
def get_imdb_id_from_tmdb(tmdb_id: str) -> Optional[str]:
    """Fetch IMDB ID for a TV show from TMDB API"""
    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/external_ids"
    params = {
        "api_key": os.getenv('TMDB_API_KEY')
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            imdb_id = data.get('imdb_id')
            if imdb_id:
                logger.info(f"Found IMDB ID {imdb_id} for TMDB ID {tmdb_id}")
                return imdb_id
            else:
                logger.error(f"No IMDB ID found for TMDB ID {tmdb_id}")
                return None
        else:
            logger.error(f"Failed to get external IDs from TMDB: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error fetching IMDB ID from TMDB: {e}")
        return None

def search_on_debrid(title: str, driver, media_type: str = 'movie', season: int = None, tmdb_id: str = None) -> bool:
    """Search for content on Debrid Media Manager"""
    try:
        logger.info(f"Starting Selenium automation for {media_type}: {title}")
        
        if media_type == 'tv' and tmdb_id:
            imdb_id = get_imdb_id_from_tmdb(tmdb_id)
            if not imdb_id:
                logger.error(f"Could not find IMDB ID for TMDB ID {tmdb_id}")
                return False
                
            show_url = f"https://debridmediamanager.com/show/{imdb_id}/{season}"
            logger.info(f"Navigating to show URL: {show_url}")
            driver.get(show_url)
            time.sleep(5)  # Increased wait time
            
            # Log the page source to see what we're working with
            logger.debug(f"Page title: {driver.title}")
            logger.debug("Looking for result boxes...")
            
            # Try to find any elements to verify page loaded
            try:
                # First check if page has loaded at all
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                logger.debug("Page body found")
                
                # Look for the specific container that holds the releases
                containers = driver.find_elements(By.CSS_SELECTOR, "div.bg-gray-800")
                logger.debug(f"Found {len(containers)} gray containers")
                
                for container in containers:
                    logger.debug(f"Container text: {container.text[:100]}...")  # Log first 100 chars
                    
                    # Check if this container has the Instant RD button
                    buttons = container.find_elements(By.CSS_SELECTOR, "button[title='Instant RD']")
                    if buttons:
                        logger.info("Found Instant RD button")
                        buttons[0].click()
                        logger.success("Clicked Instant RD button")
                        time.sleep(1)
                        return True
                    else:
                        logger.debug("No Instant RD button found in this container")
                
                logger.warning("No containers with Instant RD buttons found")
                return False
                
            except Exception as e:
                logger.error(f"Error finding elements: {str(e)}")
                # Log the current page source when there's an error
                logger.debug(f"Current page source: {driver.page_source[:500]}...")  # First 500 chars
                return False
                
        else:
            # Existing movie logic...
            pass
            
    except Exception as e:
        logger.error(f"Error in search_on_debrid: {str(e)}")
        return False

async def get_user_input():
    try:
        # Check if running in a Docker container or non-interactive environment
        if os.getenv("RUNNING_IN_DOCKER", "false").lower() == "true":
            print("Running in Docker, automatically selecting 'y' for recurring Overseerr check.")
            return 'y'  # Automatically return 'yes' when running in Docker

        # Simulate asynchronous input with a timeout for interactive environments
        user_input = await asyncio.wait_for(
            asyncio.to_thread(input, "Do you want to start the initial check and recurring task? (y/n): "), 
            timeout=10
        )
        return user_input.strip().lower()

    except asyncio.TimeoutError:
        print("Input timeout. Defaulting to 'n'.")
        return 'n'  # Default to 'no' if no input is provided within 10 seconds

    except EOFError:
        # Handle cases where no input is provided (e.g., non-interactive environment)
        print("No input received (EOFError). Defaulting to 'n'.")
        return 'n'

### Webhook Endpoint ###
@app.post("/jellyseer-webhook/")
async def jellyseer_webhook(request: Request):
    try:
        # Get the raw JSON first to inspect it
        raw_data = await request.json()
        logger.info(f"Received webhook data: {raw_data}")
        
        payload = WebhookPayload(**raw_data)
        
        # Handle based on media type
        if payload.media.media_type == "movie":
            return await process_movie_request(payload)
        elif payload.media.media_type == "tv":
            # For TV shows, we need the TMDB ID, not TVDB ID
            tmdb_id = payload.media.tmdbId
            if not tmdb_id:
                logger.error("No TMDB ID found for TV show request")
                return {"status": "error", "message": "No TMDB ID found"}
                
            return await process_tv_request(payload)
        else:
            logger.error(f"Unsupported media type: {payload.media.media_type}")
            raise HTTPException(status_code=400, detail="Unsupported media type")
            
    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def schedule_token_refresh():
    """Schedule the token refresh every 10 minutes."""
    scheduler.add_job(check_and_refresh_access_token, 'interval', minutes=10)
    logger.info("Scheduled token refresh every 10 minutes.")

### Background Task to Process Overseerr Requests Periodically ###
@app.on_event("startup")
async def startup_event():
    global driver
    logger.info("Starting SeerrBridge...")
    
    # Check and refresh access token before any other initialization
    check_and_refresh_access_token()

    # Always initialize the browser when the bot is ready
    try:
        await initialize_browser()
    except Exception as e:
        logger.error(f"Failed to initialize browser: {e}")
        return

    # Start the request processing task
    await process_requests()
    logger.info("Completed initial check of requests.")
    
    # Schedule recurring checks
    if os.getenv('DOCKER_CONTAINER', 'false').lower() == 'true':
        logger.info("Running in Docker, automatically selecting 'y' for recurring Overseerr check.")
        schedule_recheck_requests()

def schedule_recheck_requests():
    # Correctly schedule the job with the REFRESH_INTERVAL_MINUTES configured interval.
    scheduler.add_job(process_requests, 'interval', minutes=REFRESH_INTERVAL_MINUTES)
    logger.info(f"Scheduled rechecking requests every {REFRESH_INTERVAL_MINUTES} minute(s).")



async def on_close():
    await shutdown_browser()  # Ensure browser is closed when the bot closes

def get_tv_details_from_tmdb(series_id: str) -> Optional[dict]:
    """Fetch TV show details from TMDB API"""
    url = f"https://api.themoviedb.org/3/tv/{series_id}"
    params = {
        "api_key": os.getenv('TMDB_API_KEY')
    }
    
    try:
        logger.info(f"Fetching TV show details for series_id: {series_id}")
        response = requests.get(url, params=params, timeout=10)
        logger.info(f"TMDB TV API response status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            result = {
                "title": data.get('name'),  # TV shows use 'name' instead of 'title'
                "year": datetime.strptime(data.get('first_air_date', ''), '%Y-%m-%d').year if data.get('first_air_date') else None,
                "seasons": data.get('number_of_seasons', 0)
            }
            logger.info(f"Successfully retrieved TV show details: {result}")
            return result
        else:
            logger.error(f"TMDB TV API response content: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error fetching TV show details from TMDB API: {e}")
        return None

async def process_tv_request(payload: WebhookPayload):
    try:
        series_id = payload.media.tmdbId
        request_id = payload.request.request_id
            
        logger.info(f"Processing TV request for series_id {series_id} with request_id {request_id}")
        
        # Get show details from TMDB
        show_details = get_tv_details_from_tmdb(series_id)
        if not show_details:
            logger.error(f"Failed to fetch TV show details for series_id {series_id}")
            return {"status": "error", "message": "Failed to fetch TV show details"}

        show_title = show_details['title']
        logger.info(f"Processing TV show request: {show_title} ({show_details['seasons']} seasons)")
        
        # Extract requested seasons
        requested_seasons = []
        if payload.extra:
            for extra in payload.extra:
                if extra.name == 'Requested Seasons':
                    seasons_str = extra.value
                    requested_seasons = [int(s.strip()) for s in seasons_str.split(',') if s.strip().isdigit()]
                    break
        
        logger.info(f"Requested seasons: {requested_seasons}")
        
        successful_seasons = []
        failed_seasons = []
        
        for season in requested_seasons:
            try:
                confirmation_flag = await asyncio.wait_for(
                    asyncio.to_thread(search_on_debrid, show_title, driver, 'tv', season, series_id),
                    timeout=60.0
                )
                
                if confirmation_flag:
                    successful_seasons.append(season)
                    logger.info(f"Successfully processed season {season}")
                else:
                    failed_seasons.append(season)
                    logger.warning(f"Failed to find season {season}")
                    
            except asyncio.TimeoutError:
                logger.error(f"Timeout while processing season {season}")
                failed_seasons.append(season)
            except Exception as ex:
                logger.error(f"Error processing season {season}: {ex}")
                failed_seasons.append(season)
        
        # Mark as completed only if all requested seasons were successful
        if successful_seasons and not failed_seasons and request_id:
            media_id = get_media_id_from_request(request_id)
            if media_id:
                if mark_completed(media_id, series_id):
                    logger.success(f"Successfully marked TV show {media_id} as completed in overseerr")
                else:
                    logger.error(f"Failed to mark TV show {media_id} as completed in overseerr")
        
        return {
            "status": "success" if not failed_seasons else "partial" if successful_seasons else "error",
            "tmdb_id": series_id,
            "request_id": request_id,
            "title": show_title,
            "successful_seasons": successful_seasons,
            "failed_seasons": failed_seasons,
            "message": f"Processed {len(successful_seasons)} of {len(requested_seasons)} seasons successfully"
        }
        
    except Exception as e:
        logger.error(f"Error processing TV show request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Main entry point for running the FastAPI server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8777)

